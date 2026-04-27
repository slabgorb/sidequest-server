"""View projection helpers extracted from WebSocketSessionHandler.

Phase 2 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes ``handler: WebSocketSessionHandler`` as its first
argument (or operates on read-only inputs in the case of
``is_hidden_status_list``). No new abstractions introduced — this is pure
extraction with byte-identical behavior to the original methods on
WebSocketSessionHandler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.game.character import Character
    from sidequest.game.projection.view import SessionGameStateView
    from sidequest.game.status import Status
    from sidequest.protocol.messages import PartyStatusMessage
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData
