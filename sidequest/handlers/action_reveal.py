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
        sd = session._session_data
        if sd is None:
            return []

        snapshot = session._room.snapshot()
        if snapshot is None:
            logger.warning(
                "action_reveal received before room bound to world; dropping"
            )
            return []

        payload: ActionRevealPayload = msg.payload  # type: ignore[attr-defined]
        round_no = snapshot.turn_manager.round
        logger.debug(
            "action_reveal handle status=%s player_id=%s round=%d",
            payload.status,
            sd.player_id,
            round_no,
        )

        stamped = payload.model_copy(
            update={
                "round": round_no,
                "player_id": sd.player_id,
            }
        )
        outbound = ActionRevealMessage(payload=stamped, player_id=sd.player_id)
        session._room.broadcast(outbound, exclude_socket_id=session._socket_id)
        return []


HANDLER = ActionRevealHandler()
