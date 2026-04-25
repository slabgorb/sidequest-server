"""End-to-end wiring for the lore Phase 2 bundle.

Drives ``_apply_narration_result_to_snapshot`` with a
``NarrationTurnResult`` carrying ``lore_established`` strings through a
real ``TracerProvider`` + ``WatcherSpanProcessor`` and asserts the typed
``lore_retrieval`` event with ``component=lore`` reaches the hub via
``SPAN_ROUTES[SPAN_LORE_ESTABLISHED]`` — i.e. the production code path
actually opens the span.

Per ``CLAUDE.md`` "Verify Wiring, Not Just Existence": the unit test in
``tests/server/test_watcher_events.py`` proves the route extracts the
right fields from a fake span; this proves a real narration apply opens
that span. Before this bundle the GM panel's Lore tab received nothing
when the narrator established new canonical lore — only the existing
``character_creation_seed`` and retrieval-failure paths emitted.

Uses the same ``spans_module.tracer`` monkeypatch shape as the prior
inventory / NPC / state-patch wiring tests — OTEL refuses to replace
an already-installed global provider mid-suite, so patching the
function the helper actually calls is the order-independent seam.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.session import GameSnapshot, TurnManager
from sidequest.server.narration_apply import _apply_narration_result_to_snapshot
from sidequest.server.watcher import WatcherSpanProcessor
from sidequest.telemetry import spans as spans_module
from sidequest.telemetry.watcher_hub import watcher_hub


def _make_snapshot(*, lore: list[str] | None = None) -> GameSnapshot:
    return GameSnapshot(
        genre_slug="mutant_wasteland",
        world_slug="flickering_reach",
        location="Tood's Dome",
        discovered_regions=["Tood's Dome"],
        npc_registry=[],
        quest_log={},
        lore_established=list(lore or []),
        characters=[],
        turn_manager=TurnManager(),
    )


async def _setup(monkeypatch: pytest.MonkeyPatch, label: str) -> list[dict]:
    """Bind the module hub to this loop, install a local TracerProvider
    with the ``WatcherSpanProcessor``, and monkeypatch
    ``spans_module.tracer`` so the production helper resolves to it."""
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
async def test_lore_established_emits_lore_retrieval_via_span_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NarrationTurnResult with ``lore_established`` must reach the
    hub as a routed ``lore_retrieval`` event (component=lore,
    op=appended), proving ``narration_apply.py`` opens
    ``lore_established_span``."""
    captured = await _setup(monkeypatch, "test-lore-established-wiring")

    snapshot = _make_snapshot()
    snapshot.turn_manager.record_interaction()

    new_lore = "The reactor in Tood's Dome predates the Old Ones."
    result = NarrationTurnResult(
        narration="Vex spits dust, then says it.",
        lore_established=[new_lore],
    )
    _apply_narration_result_to_snapshot(snapshot, result, player_name="Rux")
    await asyncio.sleep(0.05)

    # Snapshot must have been mutated — the new lore string is canonical.
    assert new_lore in snapshot.lore_established

    typed = [
        e
        for e in captured
        if e["event_type"] == "lore_retrieval"
        and e["component"] == "lore"
        and e["fields"].get("op") == "appended"
    ]
    assert len(typed) == 1, (
        "expected exactly one appended lore_retrieval event "
        f"(got {len(typed)}: {[e['fields'] for e in typed]})"
    )
    fields = typed[0]["fields"]
    assert fields["field"] == "lore_established"
    assert fields["reason"] == "narrator_established"
    assert fields["added_count"] == 1
    assert fields["total"] == 1
    # JSON-encoded — OTEL drops list attributes silently otherwise.
    assert fields["items"] == json.dumps([new_lore])
    assert fields["player_name"] == "Rux"
    assert fields["turn_number"] == snapshot.turn_manager.interaction


@pytest.mark.asyncio
async def test_lore_established_dedupes_against_existing_snapshot_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``narration_apply.py`` only appends lore strings that are not
    already in ``snapshot.lore_established``. The route must reflect
    the post-mutation outcome — ``items`` and ``added_count`` carry
    only the genuinely new entries, even when the narrator re-asserts
    canonical statements the snapshot already records."""
    captured = await _setup(monkeypatch, "test-lore-dedupe-wiring")

    pre_existing = "The reactor in Tood's Dome predates the Old Ones."
    snapshot = _make_snapshot(lore=[pre_existing])
    snapshot.turn_manager.record_interaction()

    fresh_lore = "The Whispergrass scavengers know the reactor's pulse."
    result = NarrationTurnResult(
        narration="A second canonical fact lands.",
        lore_established=[pre_existing, fresh_lore],
    )
    _apply_narration_result_to_snapshot(snapshot, result, player_name="Rux")
    await asyncio.sleep(0.05)

    # No duplicate inserted; snapshot total advances by exactly one.
    assert snapshot.lore_established.count(pre_existing) == 1
    assert fresh_lore in snapshot.lore_established
    assert len(snapshot.lore_established) == 2

    typed = [
        e
        for e in captured
        if e["event_type"] == "lore_retrieval"
        and e["component"] == "lore"
        and e["fields"].get("op") == "appended"
    ]
    assert len(typed) == 1
    fields = typed[0]["fields"]
    # Only the genuinely new entry appears in the routed event.
    assert fields["added_count"] == 1
    assert fields["total"] == 2
    assert fields["items"] == json.dumps([fresh_lore])


@pytest.mark.asyncio
async def test_lore_route_is_single_source_no_double_emission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §6.6 dedupe rule: ``narration_apply.py`` must not also
    publish a direct ``_watcher_publish`` for the lore-established
    block — the span helper is the single source. (The pre-bundle
    code path emitted nothing here, so a regression would be a
    spurious add.)"""
    captured = await _setup(monkeypatch, "test-lore-single-source")

    snapshot = _make_snapshot()
    snapshot.turn_manager.record_interaction()

    result = NarrationTurnResult(
        narration="Canonical truth.",
        lore_established=["Mira survived the second irradiation."],
    )
    _apply_narration_result_to_snapshot(snapshot, result, player_name="Rux")
    await asyncio.sleep(0.05)

    lore_events = [
        e
        for e in captured
        if e["event_type"] == "lore_retrieval"
        and e["component"] == "lore"
    ]
    assert len(lore_events) == 1, (
        "expected exactly one lore_retrieval event for component=lore "
        f"(got {len(lore_events)}: {lore_events})"
    )
