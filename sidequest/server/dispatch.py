"""PLAYER_ACTION → Orchestrator → NARRATION dispatch.

Phase 1 single-function facade used by WebSocketSessionHandler.
All dispatch logic lives in session_handler.py — this module re-exports
the handler factory and the dispatch function for test consumers that want
to drive dispatch directly without going through the WebSocket layer.
"""

from __future__ import annotations

from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _apply_narration_result_to_snapshot,
)

__all__ = [
    "WebSocketSessionHandler",
    "_apply_narration_result_to_snapshot",
]
