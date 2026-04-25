"""OTEL span → WebSocket bridge for the GM dashboard.

Implements the `/ws/watcher` stream the UI's `useWatcherSocket` hook expects.
Every OTEL span that closes in this process is serialized into the TypeScript
``WatcherEvent`` shape (see ``sidequest-ui/src/types/watcher.ts``) and fanned
out to every connected watcher WebSocket. The generic fan-out yields
``agent_span_close`` events; semantic events (``turn_complete``,
``state_transition``, ``game_state_snapshot``, ``prompt_assembled``,
``lore_retrieval``) are published explicitly by subsystem code via
:func:`~sidequest.telemetry.watcher_hub.publish_event`.

The hub itself lives in ``sidequest.telemetry.watcher_hub`` to keep it
fastapi-free — subsystem modules must be able to publish without forcing
uvicorn's logging reconfiguration on test processes.

Threading model: FastAPI runs on the asyncio event loop. The OTEL SDK calls
``on_end`` from a background thread inside ``BatchSpanProcessor``. The hub
therefore uses :func:`asyncio.run_coroutine_threadsafe` to hop the broadcast
back onto the FastAPI loop where the WebSocket sends happen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanProcessor

from sidequest.telemetry.watcher_hub import (
    WatcherHub,
    publish_event,
    watcher_hub,
)

logger = logging.getLogger(__name__)

__all__ = [
    "WatcherHub",
    "WatcherSpanProcessor",
    "publish_event",
    "watcher_endpoint",
    "watcher_hub",
]


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

        severity = "info"
        if span.status is not None and span.status.status_code.name == "ERROR":
            severity = "error"

        # Always emit the flat firehose event — Timeline / Timing tabs depend on it.
        self._hub.publish(
            {
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
        )

        # Then, if the span has a routing decision, emit the typed event too.
        from sidequest.telemetry.spans import SPAN_ROUTES

        route = SPAN_ROUTES.get(span.name)
        if route is None:
            return

        try:
            fields = route.extract(span)
        except Exception as exc:  # noqa: BLE001
            # Per CLAUDE.md: no silent fallbacks. Surface the failure on the bus
            # so the operator sees that the translator is broken, not silently
            # missing typed events.
            logger.exception("watcher.route_extract_failed span=%s", span.name)
            self._hub.publish(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "component": "watcher",
                    "event_type": "validation_warning",
                    "severity": "error",
                    "fields": {
                        "check": "route_extract",
                        "span": span.name,
                        "error": str(exc),
                    },
                }
            )
            return

        # Inferred severity per spec §6.5.
        typed_severity = severity
        if route.event_type == "json_extraction_result":
            tier = fields.get("tier")
            if isinstance(tier, int) and tier > 1:
                typed_severity = "warning"
        # Span-attribute escape hatch for routes that need warning-grade
        # state_transition (e.g. NPC identity drift). The translator
        # otherwise can't express "warning" because OK/ERROR are the
        # only two OTEL Status states. Set ``severity`` as a span
        # attribute in the helper to opt in.
        attr_severity = attrs.get("severity")
        if isinstance(attr_severity, str) and attr_severity in {"info", "warning", "error"}:
            typed_severity = attr_severity

        self._hub.publish(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "component": route.component,
                "event_type": route.event_type,
                "severity": typed_severity,
                "fields": fields,
            }
        )

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
    try:
        # Hello frame so the client knows the stream is live before the
        # first real span arrives. Useful during idle moments when the
        # server isn't narrating. If the client already hung up (races
        # with the handshake are common during Vite HMR reloads), treat
        # it exactly like any other mid-session disconnect: evict via
        # ``finally`` and return, rather than propagating a traceback
        # through the ASGI stack.
        try:
            await websocket.send_json(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "component": "sidequest-server",
                    "event_type": "agent_span_open",
                    "severity": "info",
                    "fields": {
                        "name": "watcher.connected",
                        **hub.stats(),
                    },
                }
            )
        except WebSocketDisconnect:
            return

        while True:
            # The watcher stream is one-way server→client. Read to
            # detect client disconnect; discard the content.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(websocket)
