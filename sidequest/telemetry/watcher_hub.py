"""Process-wide pub/sub for WatcherEvent broadcasts.

Lives in `sidequest.telemetry` rather than `sidequest.server` so subsystem
code (orchestrator, game, genre) can publish semantic events without
pulling in FastAPI / uvicorn at import time. Importing
`sidequest.server.watcher` would trigger `sidequest.server.__init__`,
which imports `app.py`, which imports `uvicorn` — and uvicorn
reconfigures logging handlers at import, breaking pytest's caplog
fixture.

The FastAPI-facing pieces (WebSocket endpoint, OTEL SpanProcessor) live
in `sidequest.server.watcher` and import from here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _Sendable(Protocol):
    """Structural stand-in for ``fastapi.WebSocket`` — anything with
    ``send_json``. Keeps this module free of fastapi."""

    async def send_json(self, data: dict[str, Any]) -> None: ...


class WatcherHub:
    """Thread-safe pub/sub for WatcherEvent broadcasts."""

    def __init__(self) -> None:
        self._subscribers: set[_Sendable] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the FastAPI event loop so background-thread publishers
        can hop onto it. Called once during app startup."""
        self._loop = loop

    async def subscribe(self, ws: _Sendable) -> None:
        async with self._lock:
            self._subscribers.add(ws)
        logger.info("watcher.subscribed total=%d", len(self._subscribers))

    async def unsubscribe(self, ws: _Sendable) -> None:
        async with self._lock:
            self._subscribers.discard(ws)
        logger.info("watcher.unsubscribed total=%d", len(self._subscribers))

    def publish(self, event: dict[str, Any]) -> None:
        """Broadcast an event to all subscribers.

        Safe to call from any thread. If the event loop isn't bound yet
        (process start-up race), drop the event — the dashboard treats
        the stream as lossy by design.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers)
        dead: list[_Sendable] = []
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 — broadcast is best-effort
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers.discard(ws)


# Module-level singleton. FastAPI runs one app per process, so a single
# hub is correct by construction. Subsystem code that needs to publish
# semantic events (`turn_complete`, `state_transition`, etc.) imports
# this and calls :func:`publish_event` — no dependency injection
# required. Safe to import at module load: `publish` is a no-op until
# :meth:`WatcherHub.bind_loop` runs during FastAPI startup.
watcher_hub: WatcherHub = WatcherHub()


def publish_event(
    event_type: str,
    fields: dict[str, Any],
    *,
    component: str = "sidequest-server",
    severity: str = "info",
) -> None:
    """Publish a semantic WatcherEvent to the dashboard.

    Matches the TypeScript ``WatcherEvent`` shape (see
    ``sidequest-ui/src/types/watcher.ts``). Safe to call from any thread;
    drops silently if the hub has no bound event loop yet (process
    startup race) or no subscribers (dashboard closed).

    :param event_type: One of the ``WatcherEventType`` union members
        (``turn_complete``, ``state_transition``, ``game_state_snapshot``,
        ``prompt_assembled``, ``lore_retrieval``, etc.).
    :param fields: Event-specific fields. Schema per event type is
        defined on the TypeScript side; keep keys stable.
    :param component: Subsystem label — drives the Subsystems tab's
        component grouping. Examples: ``orchestrator``, ``npc_registry``,
        ``state.location``, ``prompt_builder``, ``rag``.
    :param severity: ``info`` | ``warning`` | ``error``.
    """
    watcher_hub.publish(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": component,
            "event_type": event_type,
            "severity": severity,
            "fields": fields,
        }
    )
