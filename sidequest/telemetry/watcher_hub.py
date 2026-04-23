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

Reload safety: under ``uvicorn --reload`` the module is re-imported
whenever source files change. A naïve module-level ``watcher_hub =
WatcherHub()`` would create a fresh singleton on every reload, orphaning
any OTEL span processors registered against the previous instance and
turning the dashboard deaf once the first reload fires. We pin the hub
to a builtins attribute so the same instance survives re-imports of
this module within the same interpreter.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_BUILTINS_HUB_ATTR = "_sidequest_watcher_hub_singleton"


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
        # Health counters so the hub can surface its own liveness via
        # `watcher.health` events (see :func:`publish_event`). A
        # self-observing observability layer is the point of the GM panel
        # — if the bus is silent, the operator needs to see WHY.
        self._published_count: int = 0
        self._dropped_count: int = 0

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
            self._dropped_count += 1
            return
        self._published_count += 1
        asyncio.run_coroutine_threadsafe(self._broadcast(event), loop)

    def stats(self) -> dict[str, int]:
        """Snapshot of broadcast counters. Exposed so the GM dashboard
        can confirm the bus is alive without grepping the server log."""
        return {
            "subscribers": len(self._subscribers),
            "published": self._published_count,
            "dropped": self._dropped_count,
        }

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
            logger.info(
                "watcher.subscribers_pruned count=%d remaining=%d",
                len(dead),
                len(self._subscribers),
            )


# Module-level singleton. FastAPI runs one app per process, so a single
# hub is correct by construction. Subsystem code that needs to publish
# semantic events (`turn_complete`, `state_transition`, etc.) imports
# this and calls :func:`publish_event` — no dependency injection
# required. Safe to import at module load: `publish` is a no-op until
# :meth:`WatcherHub.bind_loop` runs during FastAPI startup.
#
# Pin the instance to ``builtins`` so ``uvicorn --reload`` — which
# re-imports changed modules — preserves the same hub across reloads.
# Without this, each reload installs a fresh hub, orphaning all OTEL
# span processors registered against the previous instance and turning
# the dashboard deaf. (Playtest 2026-04-23.)
#
# Identity check is by-name, not ``isinstance``: after ``importlib.reload``
# the new ``WatcherHub`` class is a fresh object, so an instance created
# before the reload fails ``isinstance`` against the post-reload class
# even though its interface is identical. ``type(x).__name__`` is stable.
_existing = getattr(builtins, _BUILTINS_HUB_ATTR, None)
if _existing is not None and type(_existing).__name__ == "WatcherHub":
    watcher_hub: WatcherHub = _existing  # type: ignore[assignment]
else:
    watcher_hub = WatcherHub()
    setattr(builtins, _BUILTINS_HUB_ATTR, watcher_hub)


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
