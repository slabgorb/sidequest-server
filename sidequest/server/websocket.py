"""WebSocket connection handler for sidequest-server.

Handles the /ws endpoint: accept connections, read frames, dispatch to
session_handler, write outbound messages.

Port of the WebSocket layer in sidequest-server/src/lib.rs
(handle_ws_connection, the reader/writer split).
Phase 1 only — no dice dispatch, no shared session sync, no multiplayer.

MP-02 Task 4: per-socket write queue + PLAYER_PRESENCE broadcast.
Each connection has a dedicated writer task that drains an asyncio.Queue.
The reader loop puts outbound messages into the queue instead of sending
directly, so room.broadcast() can reach other sockets' queues safely.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from sidequest.protocol import GameMessage
from sidequest.protocol.messages import (
    ErrorMessage,
    ErrorPayload,
    GamePausedMessage,
    GamePausedPayload,
    PlayerPresenceMessage,
    PlayerPresencePayload,
)
from sidequest.protocol.types import NonBlankString  # noqa: F401

if TYPE_CHECKING:
    from sidequest.server.session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


async def ws_endpoint(websocket: WebSocket, handler: WebSocketSessionHandler) -> None:
    """WebSocket connection lifecycle — accept, loop, cleanup.

    On PLAYER_ACTION: dispatch through session_handler → emit NARRATION.
    On SESSION_EVENT{connect}: bind genre/world, load or create session.
    On malformed JSON: send ERROR and close (no silent fallback).
    On disconnect: detach outbound queue, disconnect from room, broadcast
      PLAYER_PRESENCE{disconnected} to remaining players, then persist and clean up.
    """
    await websocket.accept()
    socket_id = uuid.uuid4().hex
    registry = websocket.app.state.room_registry
    out_queue: asyncio.Queue[Any] = asyncio.Queue()
    handler.attach_room_context(registry=registry, socket_id=socket_id, out_queue=out_queue)
    logger.info("ws.connection_accepted remote=%s socket=%s", websocket.client, socket_id)

    async def _writer() -> None:
        """Drain the per-socket outbound queue and send each message."""
        while True:
            msg = await out_queue.get()
            await _send_message(websocket, msg)

    writer_task = asyncio.create_task(_writer())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = GameMessage.model_validate_json(raw)  # type: ignore[arg-type]
            except (ValidationError, ValueError) as exc:
                logger.warning("ws.malformed_json error=%s raw_preview=%r", exc, raw[:200])
                await _send_error(
                    websocket,
                    f"Malformed message: {exc}",
                    reconnect_required=False,
                )
                await websocket.close(code=1003)
                return

            logger.debug("ws.message_received type=%s", msg.type)
            outbound: list[Any] = await handler.handle_message(msg)
            for outbound_msg in outbound:
                out_queue.put_nowait(outbound_msg)

    except WebSocketDisconnect as exc:
        logger.info("ws.disconnected code=%s", exc.code)
    except Exception as exc:
        # Safety net for unhandled exceptions in handler.handle_message
        # (e.g. a programmer bug, a subsystem raising before the per-handler
        # try/except wraps it). Surface a typed error frame BEFORE the
        # finally-block close so the UI sees a reason instead of silently
        # reconnecting into the same crash. Per playtest 2026-04-25 bug
        # ticket: "WebSocket exception path leaves UI stuck on Reconnecting…
        # with no surfaced reason."
        logger.exception("ws.unexpected_error error=%s", exc)
        await _send_error(
            websocket,
            f"Server error while processing message: {exc}",
            reconnect_required=False,
            code="server_error",
        )
    finally:
        writer_task.cancel()
        room = handler.current_room()
        if room is not None:
            room.detach_outbound(socket_id)
            left_player = room.disconnect(socket_id=socket_id)
            if left_player is not None:
                room.broadcast(
                    _presence_msg(left_player, "disconnected"),
                    exclude_socket_id=socket_id,
                )
                # After the disconnect presence broadcast, check whether the room
                # is now paused (MP-02 Task 6). If so, broadcast GAME_PAUSED to
                # all remaining connected players so they know narration is
                # suspended until the absent player(s) return.
                if room.is_paused():
                    absent = room.absent_seated_player_ids()
                    room.broadcast(
                        GamePausedMessage(
                            payload=GamePausedPayload(waiting_for=absent)
                        ),
                        exclude_socket_id=None,
                    )
        await handler.cleanup()
        logger.info("ws.session_cleanup_complete")


def _presence_msg(player_id: str, state: str) -> PlayerPresenceMessage:
    """Build a PLAYER_PRESENCE message for connect/disconnect events."""
    return PlayerPresenceMessage(
        payload=PlayerPresencePayload(player_id=player_id, state=state),  # type: ignore[arg-type]
    )


async def _send_message(websocket: WebSocket, msg: Any) -> None:
    """Serialize and send a protocol message object over the WebSocket.

    All outbound messages are pydantic BaseModel instances with model_dump_json().
    """
    try:
        json_str = msg.model_dump_json()
        await websocket.send_text(json_str)
    except Exception as exc:
        logger.warning("ws.send_failed type=%s error=%s", getattr(msg, "type", "?"), exc)


async def _send_error(
    websocket: WebSocket,
    message: str,
    reconnect_required: bool = False,
    *,
    code: str | None = None,
) -> None:
    """Send an ERROR message, ignoring send failures (connection may be closing)."""
    try:
        err = ErrorMessage(
            type="ERROR",  # type: ignore[arg-type]
            payload=ErrorPayload(
                message=NonBlankString(message),
                reconnect_required=reconnect_required,
                code=code,
            ),
            player_id="",
        )
        await websocket.send_text(err.model_dump_json())
    except Exception:
        pass
