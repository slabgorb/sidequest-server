"""WebSocket connection handler for sidequest-server.

Handles the /ws endpoint: accept connections, read frames, dispatch to
session_handler, write outbound messages.

Port of the WebSocket layer in sidequest-server/src/lib.rs
(handle_ws_connection, the reader/writer split).
Phase 1 only — no dice dispatch, no shared session sync, no multiplayer.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from sidequest.protocol import GameMessage
from sidequest.protocol.messages import ErrorMessage, ErrorPayload
from sidequest.protocol.types import NonBlankString  # noqa: F401

from typing import Any

if TYPE_CHECKING:
    from sidequest.server.session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


async def ws_endpoint(websocket: WebSocket, handler: "WebSocketSessionHandler") -> None:
    """WebSocket connection lifecycle — accept, loop, cleanup.

    On PLAYER_ACTION: dispatch through session_handler → emit NARRATION.
    On SESSION_EVENT{connect}: bind genre/world, load or create session.
    On malformed JSON: send ERROR and close (no silent fallback).
    On disconnect: persist and clean up.
    """
    await websocket.accept()
    socket_id = uuid.uuid4().hex
    registry = websocket.app.state.room_registry
    handler.attach_room_context(registry=registry, socket_id=socket_id)
    logger.info("ws.connection_accepted remote=%s socket=%s", websocket.client, socket_id)

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
                await _send_message(websocket, outbound_msg)

    except WebSocketDisconnect as exc:
        logger.info("ws.disconnected code=%s", exc.code)
    except Exception as exc:
        logger.exception("ws.unexpected_error error=%s", exc)
    finally:
        room = handler.current_room()
        if room is not None:
            left_player = room.disconnect(socket_id=socket_id)
            if left_player is not None:
                await handler.broadcast_presence_change(left_player=left_player)
        await handler.cleanup()
        logger.info("ws.session_cleanup_complete")


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
) -> None:
    """Send an ERROR message, ignoring send failures (connection may be closing)."""
    try:
        err = ErrorMessage(
            type="ERROR",  # type: ignore[arg-type]
            payload=ErrorPayload(
                message=NonBlankString(message),
                reconnect_required=reconnect_required,
            ),
            player_id="",
        )
        await websocket.send_text(err.model_dump_json())
    except Exception:
        pass
