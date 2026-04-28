"""DiceThrowHandler — handles DICE_THROW messages (UI-driven roll)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.server.session_handler import _State
from sidequest.server.session_helpers import _build_turn_context, _error_msg

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class DiceThrowHandler:
    """Resolve a DICE_THROW from the rolling player.

    The UI drives all rolls via confrontation beat selection: it builds
    the DiceRequest locally, auto-rolls in Rapier, and sends a single
    DICE_THROW carrying the beat_id + physics-settled faces. The server
    applies the beat, resolves the dice, broadcasts DiceRequest +
    DiceResult to the room, and then runs the narrator inline so the
    rolling player sees prose in the same round-trip.

    Returns [] — all outbound messages go through the room broadcast
    queue so every connected socket (rolling player included) sees the
    same event stream.
    """

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        from sidequest.server.dispatch.dice import (
            DiceDispatchError,
            dispatch_dice_throw,
        )

        if session._state != _State.Playing:
            return [_error_msg("Cannot process DICE_THROW: not in Playing state")]
        if session._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        sd = session._session_data
        payload = msg.payload  # type: ignore[attr-defined]
        rolling_player_id = getattr(msg, "player_id", "") or sd.player_id

        snapshot = sd.snapshot
        encounter = snapshot.encounter
        character = snapshot.characters[0] if snapshot.characters else None
        character_name = character.core.name if character is not None else "Unknown"
        stats: dict[str, int] = dict(character.stats) if character is not None else {}

        room_broadcast = None
        if session._room is not None:
            # Wrap the room's broadcast to a simple callable the dispatcher
            # can invoke without knowing about SessionRoom. exclude=None so
            # every connected socket (rolling + spectators) receives the
            # same DiceRequest + DiceResult stream.
            def _broadcast(m: object) -> None:
                assert session._room is not None  # captured under the guard above
                session._room.broadcast(m, exclude_socket_id=None)

            room_broadcast = _broadcast

        try:
            outcome = dispatch_dice_throw(
                payload=payload,
                rolling_player_id=rolling_player_id,
                character_name=character_name,
                character_stats=stats,
                encounter=encounter,
                pack=sd.genre_pack,
                session_id=f"{sd.genre_slug}:{sd.world_slug}:{sd.player_id}",
                round_number=snapshot.turn_manager.interaction,
                room_broadcast=room_broadcast,
            )
        except DiceDispatchError as exc:
            logger.warning("dice.dispatch_error error=%s", exc)
            return [_error_msg(f"Dice throw failed: {exc}")]

        # Encounter just resolved via dice — sweep Scratch off the party
        # (Playtest 2026-04-26 Bug #1: conditions never clear). The
        # narrator-beat resolution path in narration_apply.py does the
        # same sweep; both call sites must stay in sync.
        if outcome.encounter_resolved:
            from sidequest.server.status_clear import clear_scratch_on_scene_end

            clear_scratch_on_scene_end(
                snapshot,
                reason="scene_end",
                turn=snapshot.turn_manager.interaction,
            )

        # Persist the resolved outcome so follow-up narrator runs can use it
        # (Rust parity: pending_roll_outcome). Stashed on session_data for
        # the next turn's TurnContext to pick up if needed.
        sd.pending_roll_outcome = outcome.outcome
        sd.pending_roll_actor = character_name
        # Opposed-check deferral (combat fairness, 2026-04-26). When the
        # dispatcher reports the beat was deferred, stash the player roll
        # + beat_id so ``_apply_narration_result_to_snapshot`` can pick
        # them up and run the resolver inline once the narrator emits the
        # opponent's beat.
        if outcome.opposed_pending:
            sd.pending_opposed_player_d20 = outcome.opposed_player_d20
            sd.pending_opposed_player_beat_id = outcome.opposed_player_beat_id

        # Run the narrator inline with the synthesized beat-resolved action
        # so the rolling player sees prose in the same WebSocket round-trip.
        # Matches the Rust deferred-narrator intent end-to-end, collapsed to
        # a single server tick since Python's handler is sync w.r.t. the
        # read loop.
        lore_context = await session._retrieve_lore_for_turn(sd, outcome.replay_action_text)
        turn_context = _build_turn_context(sd, lore_context=lore_context, room=session._room)
        return await session._execute_narration_turn(
            sd,
            outcome.replay_action_text,
            turn_context,
        )


HANDLER = DiceThrowHandler()
