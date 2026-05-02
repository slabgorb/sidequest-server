"""E2E wiring test — scene-end through Session via WebSocketSessionHandler.

Task F of the session-aggregate strangler. Per CLAUDE.md "Every Test Suite
Needs a Wiring Test", this lives in ``tests/integration/`` and proves the
strangler-fig is reachable from a full WebSocket message lifecycle, not
just the per-handler unit harnesses in ``tests/server/``.

Drives a real YIELD message through ``WebSocketSessionHandler.handle_message``:

- ``session_handler_factory`` (re-exported into ``tests/integration``
  conftest via ``tests/server/conftest``) binds ``sd._room`` automatically
  through ``room_for(snap, slug=genre)`` — Task E.2 wiring.
- A yield-resolvable encounter is installed (single player-side actor;
  yield clears the encounter).
- A ``Scratch`` status is seeded on the player Character so the scratch
  sweep emits ``encounter.status_cleared`` alongside ``clock.advance``.
- ``handle_message`` is the production WebSocket router; the YIELD
  registry entry routes through ``YieldHandler.handle`` →
  ``handle_yield`` → ``Session.end_scene`` → scratch-sweep + clock advance.

This test asserts the dual-span emission ("did the system advance time
AND sweep scratch?") that the per-handler tests deliberately do not
cover (see comment in ``test_yield_action_session_wiring.py``).

Per spec ``docs/superpowers/specs/2026-05-01-session-aggregate-design.md``.
"""

from __future__ import annotations

import pytest

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.game.status import Status, StatusSeverity
from sidequest.protocol.messages import YieldMessage
from sidequest.server.session_handler import _State


def _install_yieldable_encounter(sd) -> None:
    """Install a single-actor encounter so YIELD resolves on the first call."""
    enc = StructuredEncounter(
        encounter_type="negotiation",
        player_metric=EncounterMetric(
            name="resolve",
            current=0,
            starting=0,
            threshold=10,
        ),
        opponent_metric=EncounterMetric(
            name="pressure",
            current=0,
            starting=0,
            threshold=10,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        actors=[
            EncounterActor(
                name="Rux",
                role="protagonist",
                side="player",
                withdrawn=False,
            ),
        ],
        resolved=False,
    )
    sd.snapshot.encounter = enc


def _seed_scratch_status(sd) -> None:
    """Stamp a Scratch status on the player Character so the scene-end
    sweep has something to clear (and emit ``encounter.status_cleared``).
    """
    sd.snapshot.characters[0].core.statuses.append(
        Status(
            text="grazed by a thrown chair",
            severity=StatusSeverity.Scratch,
            absorbed_shifts=0,
            created_turn=0,
            created_in_encounter=None,
        )
    )


@pytest.mark.asyncio
async def test_yield_via_handle_message_advances_session_clock_and_clears_scratch(
    session_handler_factory,
    otel_capture,
):
    """End-to-end: YIELD via ``handle_message`` -> Session.end_scene fires.

    Anchors the test against vacuous-pass risk:

    - ``clock_t_hours == 0.0`` BEFORE the call.
    - ``encounter.resolved == True`` AFTER (proves the front-door scene-
      end branch was actually entered — without this, the clock/span
      assertions could pass on a path that never reached Session).
    - ``clock_t_hours == 1.0`` AFTER and a ``clock.advance`` span with
      ``beat_kind="encounter"`` and ``trigger="scene-scene_end"``.
    - ``encounter.status_cleared`` span fires once for the seeded Scratch
      status — proves the scratch sweep half of ``Session.end_scene``
      also ran. Per-character status spans are emitted in
      ``server/status_clear.py``; the integration here verifies the
      whole chain reaches it from a WebSocket message.
    """
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_yieldable_encounter(sd)
    _seed_scratch_status(sd)

    # Anchor: pre-call state is the zero clock + un-resolved encounter.
    assert sd.snapshot.clock_t_hours == 0.0
    assert sd.snapshot.encounter is not None
    assert sd.snapshot.encounter.resolved is False
    assert sd._room is not None
    assert sd._room.session.clock.t_hours == 0.0
    assert any(
        s.severity == StatusSeverity.Scratch for s in sd.snapshot.characters[0].core.statuses
    )

    # Drive the production WebSocket front-door router.
    msg = YieldMessage(player_id="player-1")
    outbound = await handler.handle_message(msg)

    # Anchor: YIELD was actually accepted (no _error_msg returned).
    # The handler emits exactly one ConfrontationMessage on resolution.
    assert outbound, "handle_message returned no outbound — handler short-circuited"
    from sidequest.protocol.messages import ConfrontationMessage

    assert any(isinstance(m, ConfrontationMessage) for m in outbound), (
        f"expected ConfrontationMessage in outbound, got {[type(m).__name__ for m in outbound]}"
    )

    # Anchor: yield path reached encounter resolution (only then does
    # handle_yield call session.end_scene).
    assert sd.snapshot.encounter.resolved is True, (
        "yield must resolve the encounter — if this fails, end_scene "
        "was not called and the clock/span assertions below pass vacuously"
    )

    # Front-door wiring: Session.end_scene advanced the clock.
    assert sd._room.session.clock.t_hours == 1.0
    assert sd.snapshot.clock_t_hours == 1.0

    span_names = [s.name for s in otel_capture.get_finished_spans()]

    # clock.advance assertion (the orbital half).
    assert "clock.advance" in span_names, f"expected clock.advance span, got {span_names}"
    clock_span = next(s for s in otel_capture.get_finished_spans() if s.name == "clock.advance")
    assert clock_span.attributes["beat_kind"] == "encounter"
    assert clock_span.attributes["trigger"] == "scene-scene_end"

    # encounter.status_cleared assertion (the scratch-sweep half).
    status_cleared_count = sum(1 for n in span_names if n == "encounter.status_cleared")
    assert status_cleared_count >= 1, (
        f"expected >=1 encounter.status_cleared span (one per cleared "
        f"Scratch), got {status_cleared_count} in {span_names}"
    )

    # And the Scratch is actually gone from the character.
    assert not any(
        s.severity == StatusSeverity.Scratch for s in sd.snapshot.characters[0].core.statuses
    ), "Scratch status should have been cleared by scene-end sweep"
