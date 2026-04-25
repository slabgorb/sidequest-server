"""DICE_THROW dispatch — physics-is-the-roll resolution + beat application.

Port of the DICE_THROW arm of sidequest-api/crates/sidequest-server/src/lib.rs
(and the pure helper at dice_dispatch.rs::handle_dice_throw).

Wire flow (matches Rust):
1. Rolling client clicks a confrontation beat, UI builds ``DiceRequestPayload``
   locally (no server round-trip), auto-rolls in Rapier, reads settled faces.
2. UI sends ``DICE_THROW { request_id, throw_params, face, beat_id? }``.
3. Server (here) applies the beat to the active encounter, validates inputs,
   resolves dice from the client-reported faces, broadcasts DICE_REQUEST +
   DICE_RESULT to the room, stashes the resolved outcome + a replay-action
   on the session, and synthesizes a narrator input that describes what
   happened mechanically.
4. The session handler then runs the narrator inline so the playtest UX is
   one click → dice result + narration, not two separate user actions.

Broadcast (not return): the session room's broadcast() puts the message into
every connected socket's outbound queue. Returning these from handle_message
too would double-send to the rolling player.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sidequest.game.beat_kinds import apply_beat
from sidequest.game.dice import ResolveError, resolve_dice_with_faces
from sidequest.game.encounter import EncounterPhase, StructuredEncounter
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import BeatDef, ConfrontationDef
from sidequest.protocol.dice import (
    DiceRequestPayload,
    DiceResultPayload,
    DiceThrowPayload,
    DieGroupResult,
    DieSides,
    DieSpec,
    RollOutcome,
    ThrowParams,
)
from sidequest.protocol.messages import DiceRequestMessage, DiceResultMessage
from sidequest.protocol.types import Stat
from sidequest.server.dispatch.confrontation import find_confrontation_def
from sidequest.telemetry.spans import (
    combat_tick_span,
    emit_dice_request_sent,
    emit_dice_result_broadcast,
    emit_dice_throw_received,
    encounter_beat_applied_span,
    encounter_resolved_span,
)

logger = logging.getLogger(__name__)


class DiceDispatchError(Exception):
    """A DICE_THROW could not be resolved.

    Wrapper for validation / resolution failures so the session handler can
    surface them as ERROR messages without string-matching the exception
    chain.
    """


@dataclass(frozen=True)
class DiceThrowOutcome:
    """Result of a successful DICE_THROW dispatch.

    Carries everything the session handler needs to: broadcast the dice
    messages, stash the outcome on session state, and synthesize a narrator
    replay action.
    """

    request: DiceRequestPayload
    result: DiceResultPayload
    replay_action_text: str
    outcome: RollOutcome
    encounter_resolved: bool


def _stat_modifier(stats: dict[str, int], stat_check: str) -> int:
    """D&D-style modifier: ``floor((score - 10) / 2)``.

    Matches Rust ``(stat_val - 10) / 2`` with integer truncation. Missing
    stats default to 0 (stat score 10) — same as Rust's ``unwrap_or(0)``.
    """
    score = stats.get(stat_check)
    if score is None:
        # Try case-insensitive fallback since the character's stats dict
        # may be UPPERCASE or TitleCase depending on the genre pack.
        for k, v in stats.items():
            if k.upper() == stat_check.upper():
                score = v
                break
    if score is None:
        return 0
    return (score - 10) // 2


def _compute_dc(beat: BeatDef) -> int:
    """Derive DC from beat ``base`` magnitude, clamped 10..=30.

    Port of Rust ``(10u32 + beat_metric_delta.unsigned_abs() * 2).clamp(10, 30)``.
    Big metric swings need bigger checks — the clamp keeps any degenerate
    pack data from producing an unreachable or trivial DC.
    """
    return max(10, min(30, 10 + abs(beat.base) * 2))


def _build_request_payload(
    *,
    request_id: str,
    rolling_player_id: str,
    character_name: str,
    stat: Stat,
    modifier: int,
    difficulty: int,
    context: str,
) -> DiceRequestPayload:
    """Shape a DiceRequest matching the client-local build so overlays sync."""
    return DiceRequestPayload(
        request_id=request_id,
        rolling_player_id=rolling_player_id,
        character_name=character_name,
        dice=[DieSpec(sides=DieSides.D20, count=1)],
        modifier=modifier,
        stat=stat,
        difficulty=difficulty,
        context=context,
    )


def _compose_result_payload(
    *,
    request: DiceRequestPayload,
    rolls: list[DieGroupResult],
    total: int,
    outcome: RollOutcome,
    seed: int,
    throw_params: ThrowParams,
) -> DiceResultPayload:
    return DiceResultPayload(
        request_id=request.request_id,
        rolling_player_id=request.rolling_player_id,
        character_name=request.character_name,
        rolls=rolls,
        modifier=request.modifier,
        total=total,
        difficulty=request.difficulty,
        outcome=outcome,
        seed=seed,
        throw_params=throw_params,
    )


def _format_replay_action(
    *,
    beat_label: str,
    stat_check: str,
    actor_side: str,
    player_metric_after: int,
    opponent_metric_after: int,
    total: int,
    outcome: RollOutcome,
) -> str:
    """Synthetic narrator input describing the mechanical beat result.

    Keeps the shape Rust produces (``[BEAT_RESOLVED] ...``) plus a roll
    summary so the narrator knows the total and outcome without having to
    re-derive them from footnotes.
    """
    return (
        f"[BEAT_RESOLVED] {beat_label} ({stat_check}, side={actor_side}): "
        f"player_momentum={player_metric_after} | "
        f"opponent_momentum={opponent_metric_after} | "
        f"Roll: {total} ({outcome.value})"
    )


def dispatch_dice_throw(
    *,
    payload: DiceThrowPayload,
    rolling_player_id: str,
    character_name: str,
    character_stats: dict[str, int],
    encounter: StructuredEncounter | None,
    pack: GenrePack,
    session_id: str,
    round_number: int,
    room_broadcast: Callable[[object], None] | None,
) -> DiceThrowOutcome:
    """Apply a beat, resolve dice, broadcast wire messages, return outcome.

    Raises ``DiceDispatchError`` when the throw can't be resolved — no
    partial state mutation leaks because beat apply only runs after stat
    validation succeeds.

    ``room_broadcast`` is the room's broadcast(msg) callable. When None
    (no room bound — e.g., legacy single-socket test paths), the dice
    messages are still built and returned on the outcome but not fanned
    out. Callers that want single-socket delivery can read them off the
    outcome.
    """
    if payload.beat_id is None:
        raise DiceDispatchError(
            "DICE_THROW missing beat_id — server-initiated dice flow is not "
            "supported in the Python port yet (UI drives all rolls via "
            "beat selection)"
        )

    if encounter is None or encounter.resolved:
        raise DiceDispatchError(
            "DICE_THROW with beat_id requires an active encounter"
        )

    cdef: ConfrontationDef | None = find_confrontation_def(
        pack.rules.confrontations if pack.rules else [],
        encounter.encounter_type,
    )
    if cdef is None:
        raise DiceDispatchError(
            f"no ConfrontationDef for encounter_type {encounter.encounter_type!r} "
            f"(pack data bug — CLAUDE.md 'no silent fallback')"
        )

    beat = next((b for b in cdef.beats if b.id == payload.beat_id), None)
    if beat is None:
        available = ",".join(b.id for b in cdef.beats)
        raise DiceDispatchError(
            f"unknown beat_id {payload.beat_id!r} for encounter "
            f"{encounter.encounter_type!r} — available: [{available}]"
        )

    # Canonicalize the stat BEFORE applying the beat so a malformed
    # stat_check doesn't leave the encounter half-applied with no dice
    # gate ever opening. Matches Rust review-cycle-2 C1.
    try:
        stat = Stat(beat.stat_check)
    except ValueError as exc:
        raise DiceDispatchError(
            f"invalid stat_check {beat.stat_check!r} on beat {payload.beat_id!r}: {exc}"
        ) from exc

    modifier = _stat_modifier(character_stats, beat.stat_check)
    difficulty = _compute_dc(beat)

    request = _build_request_payload(
        request_id=payload.request_id,
        rolling_player_id=rolling_player_id,
        character_name=character_name,
        stat=stat,
        modifier=modifier,
        difficulty=difficulty,
        context=f"{beat.label} — {beat.stat_check} check",
    )

    emit_dice_request_sent(
        request_id=request.request_id,
        rolling_player_id=request.rolling_player_id,
        stat=str(request.stat),
        difficulty=request.difficulty,
        modifier=request.modifier,
    )
    emit_dice_throw_received(
        request_id=payload.request_id,
        rolling_player_id=rolling_player_id,
        face=list(payload.face),
    )

    # Resolve dice FIRST so beat application can honor Fail/CritFail and
    # substitute ``failure_metric_delta`` when the beat declares one. The
    # earlier ordering (apply → resolve) applied ``metric_delta``
    # unconditionally, so a failed Flank still bumped momentum +3 instead of
    # paying out the declared -2 failure branch (playtest 2026-04-24).
    try:
        resolved = resolve_dice_with_faces(
            request.dice, list(payload.face), request.modifier, request.difficulty,
        )
    except ResolveError as exc:
        raise DiceDispatchError(f"dice resolution failed: {exc}") from exc

    actor = encounter.find_actor(character_name)
    if actor is None:
        # Fall back to first player-side actor when character_name doesn't
        # appear in the encounter's actor list (e.g. encounter built without
        # explicit actor registration).
        actor = next(
            (a for a in encounter.actors if a.side == "player"),
            None,
        )
    if actor is None:
        raise DiceDispatchError(
            f"character {character_name!r} not found in encounter actors "
            "and no player-side actor is present"
        )

    apply_result = apply_beat(
        encounter, actor, beat, resolved.outcome,
        turn=round_number,
    )

    if apply_result.skipped_reason:
        raise DiceDispatchError(
            f"beat {payload.beat_id!r} skipped: {apply_result.skipped_reason}"
        )

    own_delta = apply_result.deltas.own if apply_result.deltas else 0

    with encounter_beat_applied_span(
        encounter_type=encounter.encounter_type,
        actor=character_name,
        beat_id=payload.beat_id,
        metric_delta=own_delta,
    ):
        pass

    encounter_resolved = apply_result.resolved

    with combat_tick_span(
        encounter_type=encounter.encounter_type,
        beat=encounter.beat,
        phase=(encounter.structured_phase or EncounterPhase.Setup).value,
    ):
        pass
    if encounter_resolved:
        with encounter_resolved_span(
            encounter_type=encounter.encounter_type,
            outcome=encounter.outcome or "",
            source="dice_throw_beat",
        ):
            pass

    # Seed drives spectator replay animation only — face values are already
    # authoritative from the rolling player's Rapier settle.
    from sidequest.game.dice import generate_dice_seed

    seed = generate_dice_seed(session_id, round_number)
    result = _compose_result_payload(
        request=request,
        rolls=resolved.rolls,
        total=resolved.total,
        outcome=resolved.outcome,
        seed=seed,
        throw_params=payload.throw_params,
    )

    emit_dice_result_broadcast(
        request_id=result.request_id,
        rolling_player_id=result.rolling_player_id,
        total=result.total,
        outcome=result.outcome.value,
        seed=result.seed,
    )

    # Broadcast first so spectators' overlays open before the narration
    # kicks off. The server-side DiceRequest echoes the rolling player's
    # local build (same request_id); the UI is idempotent on request_id
    # so the rolling player's overlay doesn't double-open.
    if room_broadcast is not None:
        req_msg = DiceRequestMessage(payload=request, player_id="server")
        res_msg = DiceResultMessage(payload=result, player_id="server")
        room_broadcast(req_msg)
        room_broadcast(res_msg)

    replay_text = _format_replay_action(
        beat_label=beat.label,
        stat_check=beat.stat_check,
        actor_side=actor.side,
        player_metric_after=encounter.player_metric.current,
        opponent_metric_after=encounter.opponent_metric.current,
        total=resolved.total,
        outcome=resolved.outcome,
    )

    logger.info(
        "dice.throw_resolved request_id=%s rolling_player=%s total=%d outcome=%s "
        "beat_id=%s player_momentum=%d opponent_momentum=%d resolved_encounter=%s",
        request.request_id,
        rolling_player_id,
        resolved.total,
        resolved.outcome.value,
        payload.beat_id,
        encounter.player_metric.current,
        encounter.opponent_metric.current,
        encounter_resolved,
    )

    return DiceThrowOutcome(
        request=request,
        result=result,
        replay_action_text=replay_text,
        outcome=resolved.outcome,
        encounter_resolved=encounter_resolved,
    )


# Intentional re-export: callers commonly need uuid to synthesize request_ids
# when driving the dispatcher from tests / fixtures.
def new_request_id() -> str:
    """Return a fresh UUID4 string for a DiceRequest correlation id."""
    return str(uuid.uuid4())
