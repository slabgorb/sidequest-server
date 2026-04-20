"""sidequest.server — FastAPI WebSocket + REST server.

Re-exports the primary public surface for consumers and tests.
"""

from __future__ import annotations

from sidequest.server.app import create_app, main
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.dispatch import _apply_narration_result_to_snapshot

__all__ = [
    "create_app",
    "main",
    "WebSocketSessionHandler",
    "_apply_narration_result_to_snapshot",
]
