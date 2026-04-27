"""Lore RAG retrieval and embed-worker dispatch.

Phase 3 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes ``handler: WebSocketSessionHandler`` as its first
argument. No new abstractions introduced — this is pure extraction
with byte-identical behavior to the original methods on
WebSocketSessionHandler.

The ``embed_task`` lifecycle remains on ``_SessionData`` (created by
``dispatch_worker`` here, cancelled by ``WebSocketSessionHandler.cleanup``
in ``session_handler.py``). That asymmetry is intentional — the task
attribute is shared session state, not worker-module state.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

from sidequest.game.lore_embedding import (
    embed_pending_fragments,
    retrieve_lore_context,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData

logger = logging.getLogger(__name__)
