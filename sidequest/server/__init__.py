"""sidequest.server — FastAPI WebSocket + REST server.

No top-level re-exports. Importing the package was previously eager,
which pulled ``sidequest.server.app`` (and its ``uvicorn`` dep) into
every test process; uvicorn's import-time logging configuration broke
``caplog`` capture for callers that never touched FastAPI.

Consumers: import submodules directly
(``from sidequest.server.app import create_app``,
``from sidequest.server.session_handler import WebSocketSessionHandler``).
"""

from __future__ import annotations
