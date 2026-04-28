"""PlayerActionHandler — handles PLAYER_ACTION messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.game.turn import TurnPhase
from sidequest.protocol import sanitize_player_text
from sidequest.protocol.messages import (
    GamePausedMessage,
    GamePausedPayload,
    TurnStatusMessage,
    TurnStatusPayload,
)
from sidequest.protocol.types import NonBlankString
from sidequest.server.session_handler import _State
from sidequest.server.session_helpers import (
    _build_turn_context,
    _error_msg,
    _resolve_acting_character_name,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class PlayerActionHandler:
    """Handle a PLAYER_ACTION message (player narration submission).

    Sanitizes the action text, enforces the MP pause gate, broadcasts
    TURN_STATUS{active}, manages the sealed-letter barrier in multiplayer,
    and dispatches to ``_execute_narration_turn`` once all seated players
    have submitted their actions.
    """

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        if session._state not in (_State.Creating, _State.Playing):
            return [_error_msg("Cannot process PLAYER_ACTION: not connected")]

        if session._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        payload = msg.payload  # type: ignore[attr-defined]
        raw_action: str = str(payload.action)

        # Sanitize player input
        action = sanitize_player_text(raw_action)
        if not action:
            return [_error_msg("Player action is empty after sanitization")]

        # Story 3.4 Task 12: strip [combat] markers from aside-flagged actions
        # before they reach the orchestrator (port of dispatch/aside.rs).
        if getattr(payload, "aside", False):
            from sidequest.server.dispatch.combat_brackets import (
                strip_combat_brackets,
            )

            action = strip_combat_brackets(action)
            if not action:
                return [_error_msg("Player aside is empty after combat-bracket strip")]

        logger.info(
            "session.player_action genre=%s world=%s player=%s action_len=%d",
            session._session_data.genre_slug,
            session._session_data.world_slug,
            session._session_data.player_name,
            len(action),
        )

        # Pause gate (MP-02 Task 6): if any seated player is absent, return
        # GAME_PAUSED and do NOT dispatch to the narrator. This must run
        # BEFORE _execute_narration_turn so the monkeypatch gate in tests
        # confirms the method is never reached when paused. _room is None
        # when slug-connect hasn't bound a room (legacy connect path or
        # pre-connect test paths) — pause gate is a no-op in that case.
        if session._room is not None and session._room.is_paused():
            from sidequest.telemetry.spans import mp_player_action_paused_span

            absent = session._room.absent_seated_player_ids()
            player_id_attr = session._session_data.player_id if session._session_data else ""
            with mp_player_action_paused_span(
                slug=session._room.slug,
                player_id=player_id_attr,
                absent_player_ids=absent,
            ):
                logger.info(
                    "session.player_action_blocked_paused absent=%s slug=%s",
                    absent,
                    session._room.slug,
                )
            return [GamePausedMessage(payload=GamePausedPayload(waiting_for=absent))]

        # Transition to Playing on first action (handles chargen via narration)
        if session._state == _State.Creating:
            session._state = _State.Playing

        sd = session._session_data
        # MP turn-ownership signal (ADR-036 sealed-letter pacing). Broadcast
        # TURN_STATUS{status="active"} to every socket in the room so peers
        # can flip MultiplayerTurnBanner to tone="peer" while this player's
        # narration runs. Without this signal, peer tabs stayed on tone="you"
        # and gave no indication that another player was acting (playtest
        # 2026-04-25 "No peer-turn signal"). exclude_socket_id=None — the
        # actor receives it too; their banner already prefers "thinking" over
        # "you" while ``thinking=true`` is local.
        # Hoist acting_name so the buffer write below can reference it even
        # when _resolve_acting_character_name raises (falls back to player_name).
        try:
            acting_name = (
                _resolve_acting_character_name(sd, session._room)
                if session._room is not None and sd.player_name
                else sd.player_name
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session.acting_name_resolve_failed error=%s falling_back_to=%s",
                exc,
                sd.player_name,
            )
            acting_name = sd.player_name
        if session._room is not None and sd.player_name:
            try:
                turn_active_msg = TurnStatusMessage(
                    payload=TurnStatusPayload(
                        player_name=NonBlankString(acting_name),
                        status="active",
                    ),
                    player_id=sd.player_id or "",
                )
                session._room.broadcast(turn_active_msg, exclude_socket_id=None)
                logger.info(
                    "session.turn_status_active player=%s player_id=%s slug=%s",
                    acting_name,
                    sd.player_id,
                    session._room.slug,
                )
                _watcher_publish(
                    "turn_status",
                    {
                        "status": "active",
                        "player_name": acting_name,
                        "player_id": sd.player_id,
                        "slug": session._room.slug,
                    },
                    component="session",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("session.turn_status_active_broadcast_failed error=%s", exc)

        lore_context = await session._retrieve_lore_for_turn(sd, action)
        turn_context = _build_turn_context(sd, lore_context=lore_context, room=session._room)

        # ADR-036 Cinematic mode wiring. In multiplayer, every player's
        # submission goes into the per-room buffer and calls submit_input()
        # on the TurnManager barrier. If the barrier hasn't fired yet
        # (still in InputCollection), this handler returns []; another
        # player's later submission will fire the barrier and dispatch the
        # narrator with the combined action. Solo rooms (seated_player_count
        # == 1) flip the barrier on the first call and continue into the
        # elected branch immediately — zero overhead.
        if session._room is not None:
            snapshot = sd.snapshot
            session._room.record_pending_action(
                sd.player_id,
                acting_name,
                action,
            )
            snapshot.turn_manager.set_player_count(session._room.seated_player_count())
            snapshot.turn_manager.submit_input(sd.player_id)
            if snapshot.turn_manager.get_phase() != TurnPhase.InputCollection:
                # Barrier just fired on this submission — emit before the
                # dispatch CAS so a failed dispatch still leaves the
                # barrier-fired event visible.
                _watcher_publish(
                    "mp.barrier_fired",
                    {
                        "slug": session._room.slug,
                        "round": snapshot.turn_manager.round,
                        "player_count": session._room.seated_player_count(),
                        "submitter_player_id": sd.player_id,
                    },
                    component="multiplayer",
                )
            if snapshot.turn_manager.get_phase() == TurnPhase.InputCollection:
                # Still waiting on other seated players. Broadcasts already
                # delivered turn_status_active above; the dispatcher will
                # handle the actual narration when the last submission arrives.
                return []

            # Barrier fired — elect a single dispatcher per round via
            # asyncio.Lock + last_dispatched_round CAS guard.
            async with session._room.dispatch_lock:
                # CAS guard uses interaction (monotonic per-narration counter)
                # not round (which advances on narrative beats, not every turn).
                current_interaction = snapshot.turn_manager.interaction
                if session._room.last_dispatched_round >= current_interaction:
                    # Lost the race; another handler already dispatched.
                    return []
                session._room.last_dispatched_round = current_interaction
                pending = session._room.drain_pending_actions()

            _watcher_publish(
                "mp.round_dispatched",
                {
                    "slug": session._room.slug,
                    "round": snapshot.turn_manager.round,
                    "player_count": session._room.seated_player_count(),
                    "action_lengths": {pid: len(p.action) for pid, p in pending},
                    "combined_action_len": (
                        sum(len(p.action) for _, p in pending)
                        + sum(len(p.character_name) + 2 for _, p in pending)
                    ),
                },
                component="multiplayer",
            )

            combined_action = "\n".join(f"{p.character_name}: {p.action}" for _, p in pending)
            result = await session._execute_narration_turn(
                sd,
                combined_action,
                turn_context,
            )
            return result

        # Single-player path (room is None) — preserve original behavior.
        return await session._execute_narration_turn(sd, action, turn_context)


HANDLER = PlayerActionHandler()
