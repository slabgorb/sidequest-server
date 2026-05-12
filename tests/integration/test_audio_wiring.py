"""End-to-end wiring for the audio Phase 2 bundle.

Drives the production helpers ``_audio_skip`` and ``_audio_dispatched``
on ``WebSocketSessionHandler`` through a real ``TracerProvider`` +
``WatcherSpanProcessor`` and asserts the typed ``state_transition``
events with ``component=audio`` reach the hub via ``SPAN_ROUTES``.

Per ``CLAUDE.md`` "Verify Wiring, Not Just Existence": the unit tests
in ``tests/server/test_watcher_events.py`` prove the routes extract the
right fields from a fake span; this proves the production helpers
actually open those spans. These two helpers are the high-volume audio
emissions (fire once per turn) — the lower-volume
``_build_audio_backend`` path is covered by the translator-routing
tests since its helper call shape is trivial.

Uses the same ``spans_module.tracer`` monkeypatch shape as the prior
inventory / NPC / state-patch wiring tests — OTEL refuses to replace
an already-installed global provider mid-suite.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider

from sidequest.game.session import GameSnapshot, TurnManager
from sidequest.protocol.messages import AudioCuePayload
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.watcher_hub import watcher_hub


def _make_session_data() -> object:
    """Build a stand-in for ``_SessionData`` exposing only what
    ``_audio_skip`` / ``_audio_dispatched`` actually read:
    ``sd.snapshot.turn_manager.interaction``. Tests read the
    ``interaction`` counter after construction to assert against the
    actual value (TurnManager defaults to 1 and we don't advance)."""
    snapshot = GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome",
        discovered_regions=["Tood's Dome"],
        quest_log={},
        lore_established=[],
        characters=[],
        turn_manager=TurnManager(),
    )
    sd = MagicMock()
    sd.snapshot = snapshot
    return sd


async def _setup(monkeypatch: pytest.MonkeyPatch, label: str) -> list[dict]:
    """Bind hub, attach capturing subscriber, install a local
    TracerProvider with WatcherSpanProcessor, and patch
    ``spans_module.tracer`` so the production helpers resolve to it."""
    watcher_hub.bind_loop(asyncio.get_running_loop())
    async with watcher_hub._lock:  # noqa: SLF001
        watcher_hub._subscribers.clear()  # noqa: SLF001

    captured: list[dict] = []

    class _Sock:
        async def send_json(self, data: dict) -> None:
            captured.append(data)

    await watcher_hub.subscribe(_Sock())  # type: ignore[arg-type]

    provider = TracerProvider()
    provider.add_span_processor(WatcherSpanProcessor(watcher_hub))
    local_tracer = provider.get_tracer(label)
    monkeypatch.setattr(spans_module, "tracer", lambda: local_tracer)

    return captured


@pytest.mark.asyncio
async def test_audio_skip_emits_state_transition_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_audio_skip(reason)`` must reach the hub as a routed
    ``state_transition`` (component=audio, op=skipped), proving the
    helper opens ``audio_skipped_span`` rather than publishing
    directly."""
    captured = await _setup(monkeypatch, "test-audio-skip-wiring")

    sd = _make_session_data()
    expected_turn = sd.snapshot.turn_manager.interaction
    # Bind self to None — _audio_skip doesn't read self. Use the
    # unbound method to side-step __init__ requirements.
    WebSocketSessionHandler._audio_skip(  # type: ignore[arg-type]
        MagicMock(),
        sd,
        "empty_cues",
    )
    await asyncio.sleep(0.05)

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "audio"
        and e["fields"].get("op") == "skipped"
    ]
    assert len(typed) == 1, (
        "expected exactly one skipped state_transition "
        f"(got {len(typed)}: {[e['fields'] for e in typed]})"
    )
    fields = typed[0]["fields"]
    assert fields["reason"] == "empty_cues"
    assert fields["turn_number"] == expected_turn
    # ``extra`` is JSON-encoded; empty dict round-trips as "{}".
    assert fields["extra"] == "{}"


@pytest.mark.asyncio
async def test_audio_skip_with_extra_dict_is_json_encoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``extra`` dict survives the OTEL primitive-types restriction
    via JSON encoding — proves the route extract returns the JSON
    string and the dashboard sees the same data the prior
    ``fields.update(extra)`` payload carried."""
    captured = await _setup(monkeypatch, "test-audio-skip-extra-wiring")

    sd = _make_session_data()
    expected_turn = sd.snapshot.turn_manager.interaction
    WebSocketSessionHandler._audio_skip(  # type: ignore[arg-type]
        MagicMock(),
        sd,
        "error",
        extra={"error": "RuntimeError"},
    )
    await asyncio.sleep(0.05)

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "audio"
        and e["fields"].get("op") == "skipped"
    ]
    assert len(typed) == 1
    fields = typed[0]["fields"]
    assert fields["reason"] == "error"
    assert fields["turn_number"] == expected_turn
    # JSON-encoded with sort_keys for stability — single-key dicts
    # don't matter for ordering but the encoding still applies.
    assert fields["extra"] == '{"error": "RuntimeError"}'


@pytest.mark.asyncio
async def test_audio_dispatched_emits_state_transition_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_audio_dispatched(payload)`` must reach the hub as a routed
    ``state_transition`` (component=audio, op=dispatched) carrying
    mood + music_track + sfx_count — the per-turn payload the GM
    panel's audio component already consumes."""
    captured = await _setup(monkeypatch, "test-audio-dispatched-wiring")

    sd = _make_session_data()
    expected_turn = sd.snapshot.turn_manager.interaction
    payload = AudioCuePayload(
        mood="tense",
        music_track="library/mutant_wasteland/tense.ogg",
        sfx_triggers=["library/mutant_wasteland/sfx/whistle.ogg"],
    )
    WebSocketSessionHandler._audio_dispatched(  # type: ignore[arg-type]
        MagicMock(),
        sd,
        payload,
    )
    await asyncio.sleep(0.05)

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "audio"
        and e["fields"].get("op") == "dispatched"
    ]
    assert len(typed) == 1, (
        "expected exactly one dispatched state_transition "
        f"(got {len(typed)}: {[e['fields'] for e in typed]})"
    )
    fields = typed[0]["fields"]
    assert fields["turn_number"] == expected_turn
    assert fields["mood"] == "tense"
    assert fields["music_track"] == "library/mutant_wasteland/tense.ogg"
    assert fields["sfx_count"] == 1


@pytest.mark.asyncio
async def test_audio_dispatched_handles_none_mood_and_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AudioCuePayload`` allows ``mood=None`` / ``music_track=None``;
    the helper must not pass ``None`` into OTEL attributes (which would
    drop the attribute and break the route extract). The helper coerces
    to empty string — span attributes are non-null and the route emits
    empty strings the dashboard already tolerates."""
    captured = await _setup(monkeypatch, "test-audio-dispatched-none")

    sd = _make_session_data()
    payload = AudioCuePayload(mood=None, music_track=None, sfx_triggers=[])
    WebSocketSessionHandler._audio_dispatched(  # type: ignore[arg-type]
        MagicMock(),
        sd,
        payload,
    )
    await asyncio.sleep(0.05)

    typed = [
        e
        for e in captured
        if e["event_type"] == "state_transition"
        and e["component"] == "audio"
        and e["fields"].get("op") == "dispatched"
    ]
    assert len(typed) == 1
    fields = typed[0]["fields"]
    # Coerced from None → "" to satisfy OTEL primitive-types restriction.
    assert fields["mood"] == ""
    assert fields["music_track"] == ""
    assert fields["sfx_count"] == 0


@pytest.mark.asyncio
async def test_audio_route_is_single_source_no_double_emission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §6.6 dedupe rule: when ``_audio_dispatched`` opens the span
    helper, the prior direct ``_watcher_publish`` for the same
    component must NOT also fire — otherwise the dashboard
    double-counts. The route is the single source."""
    captured = await _setup(monkeypatch, "test-audio-single-source")

    sd = _make_session_data()
    payload = AudioCuePayload(mood="calm", music_track="t.ogg", sfx_triggers=[])
    WebSocketSessionHandler._audio_dispatched(  # type: ignore[arg-type]
        MagicMock(),
        sd,
        payload,
    )
    await asyncio.sleep(0.05)

    audio_events = [
        e for e in captured if e["event_type"] == "state_transition" and e["component"] == "audio"
    ]
    assert len(audio_events) == 1, (
        f"expected exactly one state_transition for audio (got {len(audio_events)}: {audio_events})"
    )
