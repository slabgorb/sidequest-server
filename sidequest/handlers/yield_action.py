"""YieldHandler — handles YIELD messages (player withdraws from encounter)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sidequest.server.session_handler import _State
from sidequest.server.session_helpers import _error_msg

if TYPE_CHECKING:
    from sidequest.protocol import GameMessage
    from sidequest.server.websocket_session_handler import WebSocketSessionHandler


class YieldHandler:
    """Handle a YIELD message — player withdraws from the active encounter.

    Marks the actor withdrawn; resolves the encounter when every
    player-side actor has yielded or been taken out; refunds edge.
    Returns [] on success — encounter outcome fans out via the next
    narrator turn which reads and clears ``pending_resolution_signal``.
    """

    async def handle(
        self,
        session: WebSocketSessionHandler,
        msg: GameMessage,
    ) -> list[object]:
        from sidequest.server.dispatch.yield_action import handle_yield

        if session._state != _State.Playing:
            return [_error_msg("Cannot process YIELD: not in Playing state")]
        if session._session_data is None:
            return [_error_msg("Internal error: session data missing")]

        sd = session._session_data
        player_id = getattr(msg, "player_id", "") or sd.player_id
        player_name = sd.player_name

        try:
            handle_yield(sd.snapshot, player_id=player_id, player_name=player_name)
        except ValueError as exc:
            return [_error_msg(str(exc))]

        return []


HANDLER = YieldHandler()
