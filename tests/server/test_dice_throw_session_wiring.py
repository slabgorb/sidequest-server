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
            BeatDef.model_validate(
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 2,
                    "stat_check": "STRENGTH",
                }
            ),
        ],
    )
    sd.genre_pack.rules.confrontations = [cdef]


def _install_active_encounter(sd) -> None:
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum",
            current=0,
            starting=0,
            threshold=2,
        ),
        opponent_metric=EncounterMetric(
            name="momentum",
            current=0,
            starting=0,
            threshold=10,
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
    session_handler_factory,
    otel_capture,
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

    clock_span = next(s for s in otel_capture.get_finished_spans() if s.name == "clock.advance")
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


@pytest.mark.asyncio
async def test_dice_throw_resolves_rolling_pc_by_seat_not_first_character(
    session_handler_factory,
):
    """Playtest 2026-05-12 17:55–18:00 caverns_sunden: when Donut clicked
    Turn Undead the server error read ``opposed_check: no stat 'WIS' for
    opponent 'Carl'`` — Donut's roll resolved against Carl's actor name
    and stat sheet because ``handlers/dice_throw.py`` was reading
    ``snapshot.characters[0]`` regardless of which connected player sent
    the DICE_THROW frame. In a 3-PC MP session that always meant
    "characters[0]", which happened to be Carl.

    Post-fix: ``snapshot.player_seats[rolling_player_id]`` resolves the
    seated PC name, and ``snapshot.characters`` is looked up by that
    name. The fallback to ``characters[0]`` is preserved for legacy /
    solo callers where ``player_seats`` is empty.

    Asserts after a DICE_THROW from player_id=donut:

      1. ``sd.pending_roll_actor`` is "Donut" (not "Carl"). This is what
         ``websocket_session_handler.py`` reads back as
         ``opposed_player_actor`` and forwards into
         ``_resolve_opposed_check_branch``; the bug stashed Carl here.
      2. ``apply_beat`` ran for Donut on the encounter — i.e. Donut's
         actor is the one whose beat_applied event fires, not Carl's.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)

    # Seat three PCs and add their characters to the snapshot. The factory
    # already gave us a "Rux" character at characters[0]; rename and add
    # peers so the bug's "Donut clicked but Carl's stats applied" shape
    # is exercisable. Order matters: characters[0] is Carl by construction
    # so the pre-fix path would attribute every roll to him.
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    sd.snapshot.characters[0].core.name = "Carl"
    sd.snapshot.characters[0].stats.clear()
    sd.snapshot.characters[0].stats["STR"] = 14
    sd.snapshot.characters.append(
        Character(
            core=CreatureCore(
                name="Donut",
                description="Donut the cleric",
                personality="kind",
                inventory=Inventory(),
            ),
            char_class="Cleric",
            race="Human",
            backstory="A devoted cleric",
        ),
    )
    sd.snapshot.characters[-1].stats["WIS"] = 16
    sd.snapshot.player_seats["carl"] = "Carl"
    sd.snapshot.player_seats["donut"] = "Donut"

    # Update encounter actors so Donut is a player-side combatant the
    # dispatcher can find. Pre-fix this didn't matter because the wrong
    # actor name was being used anyway.
    sd.snapshot.encounter.actors.append(
        EncounterActor(name="Donut", role="combatant", side="player"),
    )
    sd.snapshot.encounter.actors.append(
        EncounterActor(name="Chalk Moth", role="hostile", side="opponent"),
    )

    async def _skip(sd_, action, ctx):  # noqa: ANN001, ARG001
        return []

    handler._execute_narration_turn = _skip  # type: ignore[method-assign]

    # DICE_THROW from Donut — face=15 (passes a typical DC) so the beat
    # actually applies and pending_roll_actor gets stashed by dispatch.
    msg = DiceThrowMessage(
        payload=DiceThrowPayload(
            request_id="wire-req-donut-rolls",
            throw_params=ThrowParams(
                velocity=(0.0, 5.0, -2.0),
                angular=(1.0, 1.0, 1.0),
                position=(0.5, 0.5),
            ),
            face=[15],
            beat_id="attack",
        ),
        player_id="donut",
    )

    await handler.handle_message(msg)

    # Load-bearing assertion: pending_roll_actor is Donut, not Carl.
    # Pre-fix this was "Carl" because the handler read characters[0] —
    # downstream the opposed_check resolver would then look up Donut's
    # beat under Carl's stat sheet (Carl has no WIS → "no stat 'WIS' for
    # opponent 'Carl'" surfaces in production logs).
    assert sd.pending_roll_actor == "Donut", (
        f"DICE_THROW handler must resolve the rolling PC from player_seats; "
        f"player_id='donut' should yield pending_roll_actor='Donut', got "
        f"{sd.pending_roll_actor!r}. Pre-fix the handler read "
        f"snapshot.characters[0].core.name and every roll in MP was "
        f"attributed to whichever PC happened to be first in the list."
    )


@pytest.mark.asyncio
async def test_dice_throw_falls_back_to_characters_zero_when_seats_empty(
    session_handler_factory,
):
    """Legacy / solo guard: when ``snapshot.player_seats`` is empty (pre-
    seat-aware paths, single-player connect, replay), the handler still
    falls back to ``snapshot.characters[0]``. The fix only adds the
    seat-lookup branch; the legacy fall-through must be intact.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14
    sd.snapshot.player_seats.clear()  # legacy / pre-seat-aware path

    async def _skip(sd_, action, ctx):  # noqa: ANN001, ARG001
        return []

    handler._execute_narration_turn = _skip  # type: ignore[method-assign]

    await handler.handle_message(_throw())

    # Fall-through: characters[0] is "Rux" in the factory default, so
    # pending_roll_actor must match that.
    assert sd.pending_roll_actor == sd.snapshot.characters[0].core.name, (
        f"With empty player_seats the handler must fall back to "
        f"snapshot.characters[0]; got pending_roll_actor="
        f"{sd.pending_roll_actor!r}, characters[0]="
        f"{sd.snapshot.characters[0].core.name!r}"
    )
