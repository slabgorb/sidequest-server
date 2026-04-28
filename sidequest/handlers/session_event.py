"""SessionEventHandler — handles SESSION_EVENT messages (connect/etc).

Currently the only sub-event is ``connect``, which fans out into the
ConnectHandler via the session's ``_handle_connect`` delegate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.server.session_helpers import _error_msg

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.protocol.messages import SessionEventPayload
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class SessionEventHandler:
    """Dispatcher for SESSION_EVENT sub-events (currently just ``connect``)."""

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        payload: SessionEventPayload = msg.payload  # type: ignore[attr-defined]
        event = payload.event

        if event == "connect":
            return await session._handle_connect(payload, getattr(msg, "player_id", ""))
        logger.warning("session.unknown_event event=%s", event)
        return [_error_msg(f"Unknown SESSION_EVENT event: {event}")]


HANDLER = SessionEventHandler()
