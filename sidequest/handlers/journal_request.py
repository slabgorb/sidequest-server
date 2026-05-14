"""JournalRequestHandler — replies to JOURNAL_REQUEST with the player's journal.

ADR-100 Seam C (story 50-14). Closes the server-side gap: per ADR-100 the
UI consumer (``sidequest-ui/src/hooks/useStateMirror.ts:130-155``) was
ready, but no server handler emitted ``JOURNAL_RESPONSE``. This module is
that handler.

Player-to-character resolution goes through
``snapshot.player_seats[player_id]``. Per ADR-036 a player can only
introspect their own seat — there is no ``to`` field on the request, and
no cross-player journal access is supported.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.protocol.messages import (
    JournalResponseMessage,
    JournalResponsePayload,
)
from sidequest.protocol.models import JournalEntry
from sidequest.server.session_helpers import _error_msg
from sidequest.telemetry.spans import SPAN_JOURNAL_REPLAY, tracer

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler

logger = logging.getLogger(__name__)


class JournalRequestHandler:
    """Resolve a JOURNAL_REQUEST against the bound room's snapshot."""

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        room = session._room  # noqa: SLF001
        if room is None or room.snapshot is None:
            logger.info(
                "session.message_rejected_unbound type=JOURNAL_REQUEST state=%s",
                session._state.name,  # noqa: SLF001
            )
            return [
                _error_msg(
                    "Cannot process JOURNAL_REQUEST: room not bound",
                    code="session_unbound",
                )
            ]

        player_id: str = getattr(msg, "player_id", "") or ""
        if not player_id:
            logger.warning(
                "session.journal_request_missing_player_id slug=%s",
                room.slug,
            )
            return [
                _error_msg(
                    "Cannot process JOURNAL_REQUEST: missing player_id",
                    code="invalid_player_id",
                )
            ]

        snapshot = room.snapshot
        character_name = snapshot.player_seats.get(player_id)
        if not character_name:
            logger.info(
                "session.journal_request_unseated slug=%s player_id=%s",
                room.slug,
                player_id,
            )
            return [
                _error_msg(
                    f"Cannot process JOURNAL_REQUEST: player {player_id!r} is not seated",
                    code="player_unseated",
                )
            ]

        character = next(
            (c for c in snapshot.characters if c.core.name == character_name),
            None,
        )
        if character is None:
            logger.warning(
                "session.journal_request_seat_broken slug=%s player_id=%s seat=%s",
                room.slug,
                player_id,
                character_name,
            )
            return [
                _error_msg(
                    f"Cannot process JOURNAL_REQUEST: seat {character_name!r} has no character",
                    code="seat_broken",
                )
            ]

        entries = [
            JournalEntry(
                fact_id=fact.fact_id,
                content=fact.content,
                category=fact.category,
                source=fact.source,
                confidence=fact.confidence,
                learned_turn=fact.learned_turn,
            )
            for fact in character.known_facts
        ]

        with tracer().start_as_current_span(SPAN_JOURNAL_REPLAY) as span:
            span.set_attribute("character_name", character_name)
            span.set_attribute("entry_count", len(entries))

        return [
            JournalResponseMessage(
                payload=JournalResponsePayload(entries=entries),
                player_id=player_id,
            )
        ]


HANDLER = JournalRequestHandler()
