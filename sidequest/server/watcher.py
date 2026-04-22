"""OTEL span → WebSocket bridge for the GM dashboard.

Implements the `/ws/watcher` stream the UI's `useWatcherSocket` hook expects.
Every OTEL span that closes in this process is serialized into the TypeScript
``WatcherEvent`` shape (see ``sidequest-ui/src/types/watcher.ts``) and fanned
out to every connected watcher WebSocket. No span filtering, no schema
mapping beyond the generic ``agent_span_close`` event type — the dashboard
already renders raw spans on the Timeline tab.

The bridge is intentionally generic. Per-subsystem semantic events
(``turn_complete``, ``state_transition``, etc.) can be emitted directly by
subsystem code via :meth:`WatcherHub.publish` when those events need richer
fields than the OTEL span carries.

Threading model: FastAPI runs on the asyncio event loop. The OTEL SDK calls
``on_end`` from a background thread inside ``BatchSpanProcessor``. The hub
therefore uses :func:`asyncio.run_coroutine_threadsafe` to hop the broadcast
back onto the FastAPI loop where the WebSocket sends happen.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanProcessor

logger = logging.getLogger(__name__)


class WatcherHub:
    """Thread-safe pub/sub for WatcherEvent broadcasts."""

    def __init__(self) -> None:
        self._subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the FastAPI event loop so background-thread publishers
        can hop onto it. Called once during app startup."""
        self._loop = loop

    async def subscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.add(ws)
        logger.info("watcher.subscribed total=%d", len(self._subscribers))

    async def unsubscribe(self, ws: WebSocket) -> None:
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
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 — broadcast is best-effort
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers.discard(ws)


class WatcherSpanProcessor(SpanProcessor):
    """Converts every ended OTEL span into an ``agent_span_close``
    ``WatcherEvent`` and publishes it to the hub."""

    def __init__(self, hub: WatcherHub) -> None:
        self._hub = hub

    def on_start(
        self, span: Any, parent_context: Any = None
    ) -> None:  # noqa: ARG002
        # No start-event broadcast for now — the dashboard renders spans
        # at close time only, and doubling the volume would just waste
        # bandwidth without adding insight.
        return

    def on_end(self, span: ReadableSpan) -> None:
        end_ns = span.end_time or 0
        start_ns = span.start_time or end_ns
        duration_ms = max(0, (end_ns - start_ns) // 1_000_000)
        attrs: dict[str, Any] = {}
        if span.attributes:
            for k, v in span.attributes.items():
                attrs[str(k)] = v
        severity: str = "info"
        if span.status is not None and span.status.status_code.name == "ERROR":
            severity = "error"
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": "sidequest-server",
            "event_type": "agent_span_close",
            "severity": severity,
            "fields": {
                "name": span.name,
                "duration_ms": duration_ms,
                **attrs,
            },
        }
        self._hub.publish(event)

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True


async def watcher_endpoint(websocket: WebSocket, hub: WatcherHub) -> None:
    """FastAPI WebSocket handler for ``/ws/watcher``.

    Holds the connection open and forwards every hub publish to the
    client. No incoming client messages are expected or consumed — the
    stream is one-way.
    """
    await websocket.accept()
    await hub.subscribe(websocket)
    # Hello frame so the client knows the stream is live before the first
    # real span arrives. Useful during idle moments when the server isn't
    # narrating.
    await websocket.send_json(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": "sidequest-server",
            "event_type": "agent_span_open",
            "severity": "info",
            "fields": {"name": "watcher.connected"},
        }
    )
    try:
        while True:
            # The watcher stream is one-way server→client. Read to detect
            # client disconnect; discard the content.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(websocket)
