"""ActionRevealHandler — broadcasts in-progress action visibility (ADR-036).

Wires up ACTION_REVEAL fan-out: composing/submitted messages from clients
are fanned out to peers in the same SessionRoom. Server stamps round and
player_id authoritatively. Sealed-letter barrier and CAS-guarded
dispatcher are unaffected — this is an additive visibility channel.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sidequest.protocol.messages import (
    ActionRevealMessage,
    ActionRevealPayload,
    ActionRevealStatus,
)

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)

# Server-side rate-limit floor for composing updates per (socket_id).
# Clients should debounce at 250ms; this is a safety net for buggy or
# hand-fired clients. Submitted events bypass this throttle.
_COMPOSING_FLOOR_S = 0.100


class ActionRevealHandler:
    """Handle ACTION_REVEAL: broadcast composing/submitted to peers."""

    def __init__(self) -> None:
        # Per-(socket_id) tracking. Entries naturally evict on round
        # advance (seq) and on the next composing arrival (rate-limit
        # timestamp). Socket disconnect doesn't need explicit cleanup
        # because a fresh socket gets a new socket_id.
        self._last_seq: dict[str, tuple[int, int]] = {}
        self._last_composing_t: dict[str, float] = {}

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

        # Server emits cleared on dispatch + disconnect. Clients sending
        # it are silently dropped — they cannot fabricate clears for peers.
        if payload.status == ActionRevealStatus.CLEARED:
            return []

        round_no = snapshot.turn_manager.round
        socket_id = session._socket_id or ""

        # seq monotonicity per (socket_id, round). On round advance,
        # the prior round's tuple is replaced, naturally resetting seq.
        # Rate-limit timestamp is also cleared on round advance so the
        # first composing event of a new round is never throttled.
        prev = self._last_seq.get(socket_id)
        round_advanced = prev is not None and prev[0] != round_no
        if prev is not None and prev[0] == round_no and payload.seq <= prev[1]:
            return []

        if round_advanced:
            self._last_composing_t.pop(socket_id, None)

        # Rate-limit composing only. Submitted is a discrete event.
        if payload.status == ActionRevealStatus.COMPOSING:
            now = time.monotonic()
            last_t = self._last_composing_t.get(socket_id)
            if last_t is not None and (now - last_t) < _COMPOSING_FLOOR_S:
                return []
            self._last_composing_t[socket_id] = now

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
        session._room.broadcast(outbound, exclude_socket_id=socket_id)
        self._last_seq[socket_id] = (round_no, payload.seq)
        return []


HANDLER = ActionRevealHandler()
