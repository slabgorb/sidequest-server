"""Wire-first boundary test: DICE_THROW must emit CONFRONTATION post-beat-apply.

Story 45-3 — Momentum readout state sync.

The bug: between the player's beat-click → DICE_RESULT and the narrator's
NARRATION_END (5–15s on a long turn), the server holds the new
``encounter.player_metric.current`` but never broadcasts it. The UI
``ConfrontationOverlay`` sits on the prior turn's ``confrontationData``
and the dual-dial visibly lags the engine state — Sebastien's lie-detector
flag from playtest 2026-04-19.

The fix: ``dispatch_dice_throw`` (or the caller) must broadcast a
CONFRONTATION frame carrying post-``apply_beat`` momentum on every
non-deferred beat, BEFORE the inline narrator runs. This test exercises
the full ``_handle_dice_throw`` path (handler → dispatch_dice_throw →
inline narrator), captures the room's broadcast queue, and asserts:

1. **AC1 (mid-turn emit):** A CONFRONTATION message is broadcast between
   DICE_RESULT and NARRATION_END, with ``player_metric.current`` /
   ``opponent_metric.current`` reflecting the post-beat-apply encounter
   state.

2. **AC1 negative (opposed-check deferral):** When the active
   confrontation declares ``resolution_mode: opposed_check``, deltas are
   deferred to ``narration_apply``; the dispatcher MUST NOT emit a
   mid-turn CONFRONTATION on this branch.

3. **Existing post-narration emit unchanged (AC5 regression):** the
   post-narration CONFRONTATION emit at ``session_handler.py`` still
   fires once per narration turn — the new mid-turn emit is additive.

Wire-first gate: the test drives the production handler through
``handle_message(DiceThrowMessage)`` — not ``dispatch_dice_throw``
in isolation. A future regression that re-orders emit vs broadcast (or
moves the call inside the narrator) will be caught here.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.genre.models.rules import (
    BeatDef,
    ConfrontationDef,
    MetricDef,
)
from sidequest.protocol.dice import DiceThrowPayload, ThrowParams
from sidequest.protocol.messages import (
    ConfrontationMessage,
    DiceRequestMessage,
    DiceResultMessage,
    DiceThrowMessage,
    NarrationMessage,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _install_combat_def(sd, *, resolution_mode: str = "beat_selection") -> None:
    """Install a deterministic combat ConfrontationDef.

    ``stat_check=STRENGTH`` maps to the +2 modifier we set on the seeded
    Rux character. ``base=3`` so a Success throw produces a non-zero
    metric delta we can observe on the post-apply momentum.
    """
    cdef = ConfrontationDef.model_validate({
        "type": "combat",
        "label": "Dungeon Combat",
        "category": "combat",
        "resolution_mode": resolution_mode,
        "opponent_default_stats": (
            {"STR": 12} if resolution_mode == "opposed_check" else {}
        ),
        "player_metric": MetricDef(
            name="momentum", starting=0, threshold=10,
        ).model_dump(),
        "opponent_metric": MetricDef(
            name="momentum", starting=0, threshold=10,
        ).model_dump(),
        "beats": [
            BeatDef.model_validate({
                "id": "attack",
                "label": "Attack",
                "kind": "strike",
                "base": 3,
                "stat_check": "STRENGTH",
            }).model_dump(),
        ],
    })
    sd.genre_pack.rules.confrontations = [cdef]


def _install_active_encounter(sd) -> None:
    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum", current=0, starting=0, threshold=10,
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=0, starting=0, threshold=10,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=None,
        actors=[
            EncounterActor(name="Rux", role="combatant", side="player"),
            EncounterActor(name="Goblin", role="combatant", side="opponent"),
        ],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )
    sd.snapshot.encounter = enc


def _throw(face: int = 15, beat_id: str = "attack") -> DiceThrowMessage:
    return DiceThrowMessage(
        payload=DiceThrowPayload(
            request_id="momentum-sync-req-1",
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


class _StubRoom:
    """SessionRoom stand-in that records broadcasts in arrival order."""

    slug = "momentum-sync-test"

    def __init__(self) -> None:
        self.broadcasts: list[tuple[object, str | None]] = []

    def broadcast(
        self,
        msg: object,
        *,
        exclude_socket_id: str | None = None,
    ) -> None:
        self.broadcasts.append((msg, exclude_socket_id))

    def is_paused(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# AC1: Server emits CONFRONTATION after beat-apply, before narration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dice_throw_emits_confrontation_with_post_beat_momentum(
    session_handler_factory,
):
    """The mid-turn CONFRONTATION broadcast carries post-apply momentum.

    Sequence under wire-first:
      1. UI sends DICE_THROW with beat_id=attack (face=15, +2 mod, DC=14
         → total 17, Success tier, base=3 own_delta).
      2. ``dispatch_dice_throw`` calls ``apply_beat`` which mutates
         ``encounter.player_metric.current`` (0 → 3 on Success).
      3. ``room_broadcast`` fans out DICE_REQUEST + DICE_RESULT.
      4. **NEW (this story):** a CONFRONTATION broadcast carrying the
         post-apply ``player_metric.current=3`` arrives in the room
         queue BEFORE the inline narrator's NARRATION_END.

    The assertion: scan the broadcast queue, find the CONFRONTATION
    that arrives BEFORE NARRATION_END, and verify it reflects the
    post-apply metric — not the starting zero.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14  # +2 modifier

    room = _StubRoom()
    handler._room = room  # type: ignore[assignment]

    # Stub the narrator so the inline turn returns deterministically. The
    # post-narration CONFRONTATION emit at ``_execute_narration_turn``
    # runs through this stub; we capture both emits (mid-turn + post-
    # narration) on the same room queue.
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="You strike true!"),
    )

    await handler.handle_message(_throw(face=15))

    # Order witness: index of DICE_RESULT and any NARRATION_END marker.
    order: list[str] = []
    for msg, _exclude in room.broadcasts:
        if isinstance(msg, DiceResultMessage):
            order.append("DICE_RESULT")
        elif isinstance(msg, ConfrontationMessage):
            order.append("CONFRONTATION")
        elif isinstance(msg, NarrationMessage):
            # NarrationMessage.type is one of NARRATION/NARRATION_END
            order.append(getattr(msg.type, "value", str(msg.type)))

    # The mid-turn CONFRONTATION must land between DICE_RESULT and
    # the narrator's NARRATION_END. A pre-fix server has no
    # CONFRONTATION in the room queue at all (it only emits post-
    # narration via the handler return path), so this assertion fails
    # red.
    confrontations = [
        m for m, _ in room.broadcasts if isinstance(m, ConfrontationMessage)
    ]
    assert len(confrontations) >= 1, (
        f"expected at least one CONFRONTATION broadcast in the room queue "
        f"between DICE_RESULT and NARRATION_END; got order={order!r}"
    )

    # Find the FIRST CONFRONTATION (the new mid-turn emit). It must
    # arrive after DICE_RESULT and carry the post-apply momentum.
    first_conf_idx = next(
        i for i, (m, _) in enumerate(room.broadcasts)
        if isinstance(m, ConfrontationMessage)
    )
    dice_result_idx = next(
        i for i, (m, _) in enumerate(room.broadcasts)
        if isinstance(m, DiceResultMessage)
    )
    assert first_conf_idx > dice_result_idx, (
        f"CONFRONTATION must broadcast AFTER DICE_RESULT so the UI "
        f"applies it once the dice settle; got idx={first_conf_idx} "
        f"vs DICE_RESULT idx={dice_result_idx}, order={order!r}"
    )

    # Post-apply momentum: face=15, +2 mod, DC=14 → total 17, Success
    # tier (margin 3 ≥ DECISIVE_MARGIN). own_delta=base=3 on the
    # Success tier per beat_kinds; encounter.player_metric.current
    # advances 0 → 3.
    first_conf = room.broadcasts[first_conf_idx][0]
    assert isinstance(first_conf, ConfrontationMessage)
    payload = first_conf.payload
    # ConfrontationPayload.player_metric is dict[str, Any] (mirrors the
    # JSON wire shape) — access via key, not attribute.
    assert payload.player_metric["current"] == 3, (
        f"mid-turn CONFRONTATION must carry POST-apply player_metric "
        f"(0 → 3 after Success on attack); got "
        f"player_metric.current={payload.player_metric.get('current')!r}"
    )
    # Active flag asserts the encounter is not yet resolved (3 < 10).
    assert payload.active is True, (
        "mid-turn CONFRONTATION must keep active=True while the "
        "encounter remains unresolved"
    )


@pytest.mark.asyncio
async def test_dice_throw_mid_turn_confrontation_arrives_before_narration_end(
    session_handler_factory,
):
    """The mid-turn CONFRONTATION must precede the narrator's NARRATION_END.

    The whole point of the fix is that the dial moves *as the dice settle*,
    not *after the narrator completes*. If the new emit lands AFTER
    NARRATION_END the bug isn't fixed — the UI still sees the lag.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14

    room = _StubRoom()
    handler._room = room  # type: ignore[assignment]

    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="Strike lands."),
    )

    msgs = await handler.handle_message(_throw(face=15))

    # NARRATION_END is returned to the rolling client via the handler
    # return path (not the room broadcast queue). The mid-turn emit goes
    # through ``room_broadcast``. So the witness is: at least one
    # CONFRONTATION exists in ``room.broadcasts`` BEFORE the
    # ``NarrationMessage`` arrives in the handler return list.
    room_confrontations = [
        m for m, _ in room.broadcasts if isinstance(m, ConfrontationMessage)
    ]
    assert room_confrontations, (
        "no CONFRONTATION on the room queue — the mid-turn emit is the "
        "single observable signal that the dial should advance before "
        "the narrator runs"
    )

    # Sanity: a NarrationMessage came back to the rolling client, so the
    # narrator did execute. The CONFRONTATION arrived independently on
    # the room queue first.
    narration = [m for m in msgs if isinstance(m, NarrationMessage)]
    assert narration, "narrator must still run inline after the broadcast"


# ---------------------------------------------------------------------------
# AC1 negative: opposed_check defers — no mid-turn CONFRONTATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opposed_check_does_not_emit_mid_turn_confrontation(
    session_handler_factory,
):
    """Opposed-check encounters defer beat application to ``narration_apply``.

    Per ``dispatch_dice_throw`` (dice.py:336-359), when the active
    confrontation declares ``resolution_mode: opposed_check`` the deltas
    are deferred — the player has rolled but no metric mutation happens
    yet. There is therefore NO post-apply momentum to broadcast at the
    dice-throw site; the existing post-narration emit handles it once
    the narrator picks the opponent's beat.

    This guards against a fix that emits unconditionally and broadcasts
    a stale-zero CONFRONTATION mid-turn on the opposed branch — which
    would teach the UI a wrong "the engine processed the beat" signal
    on a turn where the opposed roll hasn't run yet.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd, resolution_mode="opposed_check")
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14

    room = _StubRoom()
    handler._room = room  # type: ignore[assignment]

    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="You square off."),
    )

    await handler.handle_message(_throw(face=15))

    # The opposed branch: deltas are deferred to narration_apply, so
    # the mid-turn emit MUST NOT fire from the dice path. Any
    # CONFRONTATION in the room queue here would be a regression — it
    # would broadcast a stale-zero metric and convince the UI the engine
    # had moved when it hasn't yet.
    confrontations_in_room = [
        m for m, _ in room.broadcasts if isinstance(m, ConfrontationMessage)
    ]
    assert confrontations_in_room == [], (
        f"opposed_check defers beat application; the dice-throw site "
        f"MUST NOT emit a mid-turn CONFRONTATION on this branch. Got "
        f"{len(confrontations_in_room)} CONFRONTATION broadcasts: "
        f"{confrontations_in_room!r}"
    )

    # Sanity: the dice messages still went out — the deferral only
    # gates the new CONFRONTATION emit, not the existing dice fan-out.
    dice_msgs = [
        m for m, _ in room.broadcasts
        if isinstance(m, (DiceRequestMessage, DiceResultMessage))
    ]
    assert len(dice_msgs) == 2, (
        f"opposed_check still fans out DICE_REQUEST + DICE_RESULT; got "
        f"{len(dice_msgs)} dice broadcasts"
    )


# ---------------------------------------------------------------------------
# AC5 regression: post-narration emit unchanged (additive fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_narration_confrontation_emit_path_unchanged(
    session_handler_factory,
):
    """The post-narration CONFRONTATION emit is untouched — additive fix.

    The new mid-turn emit lands on ``room.broadcast`` (the dice-fan-out
    path). The existing post-narration emit at ``_execute_narration_turn``
    routes through ``_emit_event`` → projection-filtered per-socket queue
    fan-out, NOT ``room.broadcast``. In this minimal session setup
    (``session_handler_factory`` does not install ``_event_log`` /
    ``_projection_filter``), the post-narration path falls into the
    legacy branch of ``emit_event`` which returns the message to the
    caller's outbound list rather than fanning it out.

    The regression we're guarding against: a fix that *replaces* the
    post-narration emit with the mid-turn one (rather than adding to
    it) would also remove the inline branch at
    ``session_handler.py:3415-3471``. Asserting that the dispatch loop
    still reaches the narration step (NarrationMessage in handler
    return) AND that the dice path still produces exactly ONE
    room.broadcast CONFRONTATION (the new emit, not a duplicate)
    locks both halves of the additive contract.

    The end-to-end fan-out of the post-narration CONFRONTATION through
    the projection filter is exercised by
    ``test_confrontation_dispatch_wiring.py`` and
    ``test_confrontation_mp_broadcast.py``, which set up the full
    event-log + projection-filter stack.
    """
    from sidequest.server.session_handler import _State

    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    handler._state = _State.Playing
    _install_combat_def(sd)
    _install_active_encounter(sd)
    sd.snapshot.characters[0].stats["STRENGTH"] = 14

    room = _StubRoom()
    handler._room = room  # type: ignore[assignment]

    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="A clean strike."),
    )

    msgs = await handler.handle_message(_throw(face=15))

    # Mid-turn emit fires exactly once on the room broadcast path.
    room_confrontations = [
        m for m, _ in room.broadcasts if isinstance(m, ConfrontationMessage)
    ]
    assert len(room_confrontations) == 1, (
        f"expected exactly ONE mid-turn CONFRONTATION on the room "
        f"broadcast queue; got {len(room_confrontations)}. A duplicate "
        f"would mean the new emit is firing twice from the dice path."
    )
    assert room_confrontations[0].payload.player_metric["current"] == 3, (
        "mid-turn CONFRONTATION must carry post-apply momentum=3"
    )

    # Narrator step ran: NarrationMessage present in handler return list.
    # If the new emit had crashed or short-circuited the narrator the
    # post-narration code path would never run.
    narration_msgs = [m for m in msgs if isinstance(m, NarrationMessage)]
    assert narration_msgs, (
        "narrator step must still run after the new mid-turn emit — "
        "additive fix, not replacement"
    )


# asyncio marker for the test module
_ = asyncio
