"""Regression tests for playtest 2026-04-30 #1C — per-turn `game_state_snapshot`.

Pre-fix the event was published only at session connect / chargen
confirmation. After the initial fire the dashboard's State tab read
"Waiting for GameStateSnapshot event..." forever, and a GM panel that
attached mid-session never received any state at all (the watcher hub
has no replay buffer). Per ADR-031 the watcher should tick every turn
so the GM panel can verify state advancement; this test pins that
contract — and that the payload includes the rich snapshot dump the
State panel needs to render characters/NPCs/inventory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult


@pytest.mark.asyncio
async def test_execute_narration_turn_publishes_game_state_snapshot(
    session_handler_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: list[tuple[str, dict, str]] = []

    def fake_publish(
        event_type: str,
        payload: dict,
        *,
        component: str = "sidequest-server",
        severity: str = "info",  # noqa: ARG001
    ) -> None:
        captured.append((event_type, payload, component))

    # Patch BOTH the canonical hub address and the local re-import inside
    # the session handler — the handler imports `publish_event as
    # _watcher_publish` at module scope, so a runtime monkeypatch of the
    # hub doesn't reach the existing alias.
    monkeypatch.setattr(
        "sidequest.telemetry.watcher_hub.publish_event",
        fake_publish,
    )
    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler._watcher_publish",
        fake_publish,
    )

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="The wind picks up."),
    )

    from sidequest.server.session_handler import _build_turn_context

    await handler._execute_narration_turn(  # noqa: SLF001 — testing internal seam
        sd,
        "I look around.",
        _build_turn_context(sd),
    )

    snapshots = [c for c in captured if c[0] == "game_state_snapshot"]
    assert len(snapshots) >= 1, (
        f"_execute_narration_turn must publish at least one "
        f"`game_state_snapshot` event per turn — pre-fix the event "
        f"only fired at session connect, leaving the dashboard's "
        f"State tab stuck on `Waiting for GameStateSnapshot...`."
        f"\nActual events captured: {[c[0] for c in captured]}"
    )
    _, payload, component = snapshots[-1]
    # The event is attributed to the `game` watcher subsystem so the
    # Activity Grid lights up that row. Pre-fix the `game` row was
    # SILENT for every turn after T#0.
    assert component == "game"
    assert payload["reason"] == "turn"
    # Rich snapshot dump — the State tab reads `s.characters`, `s.npcs`,
    # `s.location`, etc. from a top-level `snapshot:` key; the back-compat
    # summary fields keep working alongside it.
    assert "snapshot" in payload, (
        "payload must include a `snapshot:` key carrying snapshot.model_dump() "
        "so the State panel can render the rich character/NPC/inventory UI"
    )
    snap = payload["snapshot"]
    assert "characters" in snap
    # Wave 2B (story 45-48): per-character location replaces the legacy
    # ``location`` field. State panel reads from this dict.
    assert "character_locations" in snap
    assert "turn_manager" in snap
    # And the existing summary fields still flow for legacy consumers.
    assert "current_location" in payload
    assert "character_count" in payload
    assert payload["turn_number"] == sd.snapshot.turn_manager.interaction


@pytest.mark.asyncio
async def test_publish_failure_does_not_crash_turn(
    session_handler_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    """The publish wrapping is in a try/except so a serialization
    issue (e.g. an exotic Python object that pydantic can't serialize)
    can't crash the hot turn path. The turn must still return its
    outbound messages even if the snapshot publish blew up.
    """

    # Existing real publisher remains the default; only the snapshot
    # publish is hot-swapped to fail. Other turn-time publishes
    # (state_transition for footnotes, render dispatch, etc.) must keep
    # flowing — we're isolating the safety wrapper around the
    # game_state_snapshot publish specifically.
    from sidequest.server import websocket_session_handler as wsh

    real_publish = wsh._watcher_publish

    def selectively_explosive_publish(
        event_type: str,
        payload: dict,
        *,
        component: str = "sidequest-server",
        severity: str = "info",
    ) -> None:
        if event_type == "game_state_snapshot":
            raise RuntimeError("watcher hub on fire")
        real_publish(event_type, payload, component=component, severity=severity)

    monkeypatch.setattr(
        "sidequest.server.websocket_session_handler._watcher_publish",
        selectively_explosive_publish,
    )

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="Quiet."),
    )

    from sidequest.server.session_handler import _build_turn_context

    # Must not raise.
    msgs = await handler._execute_narration_turn(  # noqa: SLF001
        sd,
        "Sit a moment.",
        _build_turn_context(sd),
    )
    assert msgs is not None
