"""Wiring test: DiceThrowHandler resolution path advances Session clock.

Task E.3 of the session-aggregate strangler. The dice-resolved branch in
``handlers/dice_throw.py`` previously called ``clear_scratch_on_scene_end``
directly. After E.3 it routes scene-end through
``sd._room.session.end_scene("scene_end", turn=...)``, which sweeps
Scratch AND advances the orbital clock (firing a ``clock.advance`` span).

Drives a real DICE_THROW through the production handler:

- ``session_handler_factory`` binds ``sd._room`` automatically (Task E.2)
  via ``room_for(snap, slug=genre)``.
- Install a one-strike confrontation with ``player_metric.threshold=2`` so
  a Success-tier "attack" (kind=strike, base=2) crosses the threshold and
  ``apply_beat`` flips ``encounter.resolved=True`` → the dice-handler's
  ``if outcome.encounter_resolved`` branch fires.
- Stub ``_execute_narration_turn`` so the test asserts the wiring at the
  scene-end seam, not the downstream narrator behavior.
"""
from __future__ import annotations

import pytest

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.genre.models.rules import BeatDef, ConfrontationDef, MetricDef
from sidequest.protocol.dice import DiceThrowPayload, ThrowParams
from sidequest.protocol.messages import DiceThrowMessage


def _install_combat_def(sd) -> None:
    cdef = ConfrontationDef(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        # player_metric threshold low enough that one Success-tier strike
        # (base=2) crosses it and apply_beat flips resolved=True.
        player_metric=MetricDef(name="momentum", starting=0, threshold=2),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=10),
        beats=[
            BeatDef.model_validate({
                "id": "attack",
                "label": "Attack",
                "kind": "strike",
                "base": 2,
                "stat_check": "STRENGTH",
            }),
        ],
    )
    sd.genre_pack.rules.confrontations = [cdef]


def _install_active_encounter(sd) -> None:
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum", current=0, starting=0, threshold=2,
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=0, starting=0, threshold=10,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        actors=[EncounterActor(name="Rux", role="combatant", side="player")],
        resolved=False,
    )
    sd.snapshot.encounter = enc


def _throw(face: int = 15, beat_id: str = "attack") -> DiceThrowMessage:
    return DiceThrowMessage(
        payload=DiceThrowPayload(
            request_id="wire-req-e3",
            throw_params=ThrowParams(
                velocity=(0.0, 5.0, -2.0),
                angular=(1.0, 1.0, 1.0),
                position=(0.5, 0.5),
            ),
            face=[face],
            beat_id=beat_id,
        ),
        player_id="player-1",
    )


@pytest.mark.asyncio
async def test_dice_throw_advances_clock_on_encounter_resolved(
    session_handler_factory, otel_capture,
):
    """Encounter-resolved dice outcome -> clock advances + clock.advance span fires.

    face=15, STR=14 (+2), DC=14 -> total 17 -> Success-tier strike, base=2.
    player_metric threshold=2; +2 push lands at threshold; apply_beat flips
    resolved=True; the front-door scene-end runs Session.end_scene which
    advances the clock and emits clock.advance.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14

    assert sd.snapshot.clock_t_hours == 0.0

    # Stub the narration turn so the test isolates the dice-resolved
    # scene-end wiring from the downstream narrator path. The scene-end
    # call site runs BEFORE _execute_narration_turn in dice_throw.py, so
    # this stub does not gate the migrated branch.
    async def _skip(sd_, action, ctx):  # noqa: ANN001, ARG001
        return []

    handler._execute_narration_turn = _skip  # type: ignore[method-assign]

    await handler.handle_message(_throw())

    # Anchor: dice path actually resolved the encounter.
    assert sd.snapshot.encounter.resolved, (
        "encounter must be flipped to resolved=True by apply_beat — if "
        "this fails the front-door branch was not entered and the wiring "
        "assertions below pass vacuously"
    )
    assert sd.snapshot.encounter.outcome == "player_victory"

    # Front-door wiring: end_scene was called and advanced the clock.
    assert sd._room is not None
    assert sd._room.session.clock.t_hours == 1.0
    assert sd.snapshot.clock_t_hours == 1.0

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "clock.advance" in span_names

    clock_span = next(
        s for s in otel_capture.get_finished_spans() if s.name == "clock.advance"
    )
    assert clock_span.attributes["beat_kind"] == "encounter"
    assert clock_span.attributes["trigger"] == "scene-scene_end"


@pytest.mark.asyncio
async def test_dice_throw_raises_when_room_missing(session_handler_factory):
    """Defensive guard: sd._room is None -> RuntimeError, not silent skip.

    The slug-connect branch always populates ``sd._room`` in production;
    a None here is a programming error and must surface loudly per the
    'No Silent Fallbacks' principle.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14

    # Force the impossible-but-defensive branch.
    sd._room = None

    async def _skip(sd_, action, ctx):  # noqa: ANN001, ARG001
        return []

    handler._execute_narration_turn = _skip  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="sd._room is None"):
        await handler.handle_message(_throw())
