"""Event emission helpers extracted from WebSocketSessionHandler.

Phase 1 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes `handler: WebSocketSessionHandler` as its first
argument and operates on the handler's mutable state. No new abstractions
introduced — this is pure extraction with byte-identical behavior to the
original methods on WebSocketSessionHandler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.protocol.messages import ScrapbookEntryPayload
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData
