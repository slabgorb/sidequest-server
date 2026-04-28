"""Message-handler protocol shared by every handler under this package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler


class MessageHandler(Protocol):
    """A first-class handler for one inbound message type.

    Implementations are stateless; per-session state is read off the
    ``session`` argument. The session is the
    :class:`~sidequest.server.websocket_session_handler.WebSocketSessionHandler`
    instance that owns the WebSocket connection, the active
    ``_SessionData``, the room (if any), and all collaborator clients.
    """

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]: ...
