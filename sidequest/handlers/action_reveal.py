"""ActionRevealHandler — broadcasts in-progress action visibility (ADR-036).

Wires up ACTION_REVEAL fan-out: composing/submitted messages from clients
are fanned out to peers in the same SessionRoom. Server stamps round and
player_id authoritatively. Sealed-letter barrier and CAS-guarded
dispatcher are unaffected — this is an additive visibility channel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealPayload,
)

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class ActionRevealHandler:
    """Handle ACTION_REVEAL: broadcast composing/submitted to peers."""

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        assert isinstance(msg, ActionRevealMessage)
        payload: ActionRevealPayload = msg.payload

        snapshot = session._room.snapshot()
        stamped = payload.model_copy(
            update={
                "round": snapshot.turn_manager.round,
                "player_id": session._player_id,
            }
        )
        outbound = ActionRevealMessage(payload=stamped, player_id=session._player_id)
        session._room.broadcast(outbound, exclude_socket_id=session._socket_id)
        return []


HANDLER = ActionRevealHandler()
