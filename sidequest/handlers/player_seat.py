"""PlayerSeatHandler — handles PLAYER_SEAT messages (character slot claim)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.protocol.messages import SeatConfirmedMessage, SeatConfirmedPayload

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class PlayerSeatHandler:
    """Handle a PLAYER_SEAT message (character slot claim).

    Seats the player in the room and broadcasts SEAT_CONFIRMED to all players.
    Returns empty list — the broadcast handles fan-out via the room.
    """

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        from sidequest.telemetry.spans import mp_seat_span

        payload = msg.payload  # type: ignore[attr-defined]
        player_id = getattr(msg, "player_id", "") or (
            session._session_data.player_id if session._session_data else ""
        )
        character_slot = payload.character_slot

        slug_attr = session._room.slug if session._room is not None else ""
        with mp_seat_span(
            slug=slug_attr,
            player_id=player_id,
            character_slot=character_slot,
            room_bound=session._room is not None,
        ) as _seat_span:
            # Seat the player in the room (thread-safe, idempotent)
            if session._room is not None:
                session._room.seat(player_id, character_slot=character_slot)
                logger.info(
                    "session.player_seated player_id=%s character_slot=%s slug=%s",
                    player_id,
                    character_slot,
                    session._room.slug,
                )
                _seat_span.set_attribute("seated_count", len(session._room.seated_player_ids()))
            else:
                logger.warning(
                    "session.player_seat_no_room player_id=%s character_slot=%s",
                    player_id,
                    character_slot,
                )

            # Build and broadcast SEAT_CONFIRMED to all players
            confirmed_msg = SeatConfirmedMessage(
                payload=SeatConfirmedPayload(
                    player_id=player_id,
                    character_slot=character_slot,
                ),
            )

            if session._room is not None:
                session._room.broadcast(confirmed_msg, exclude_socket_id=None)

        return []


HANDLER = PlayerSeatHandler()
