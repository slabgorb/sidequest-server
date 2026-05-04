"""OpenTelemetry tracer setup for sidequest-server.

The default destination for spans is the WatcherSpanProcessor (registered
in server/app.py). Console export is debug-only and gated behind
SIDEQUEST_OTEL_CONSOLE=1 so that normal runs don't pollute stdout with
span dumps.

OTLP export to a collector (e.g. local Jaeger v2 all-in-one) is gated
behind SIDEQUEST_OTLP_ENDPOINT=host:port. Both gates are independent —
the WatcherHub keeps feeding the GM dashboard regardless of OTLP wiring.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

logger = logging.getLogger(__name__)

_initialized = False


def init_tracer(service_name: str = "sidequest-server") -> None:
    """Initialize the global OpenTelemetry tracer provider.

    Idempotent — safe to call from tests and from app startup.
    """
    global _initialized
    if _initialized:
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Strict "1"-only gate (deliberate; not a permissive truthy check) so
    # console exporter only fires when explicitly opted-in for debug.
    if os.environ.get("SIDEQUEST_OTEL_CONSOLE") == "1":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    otlp_endpoint = os.environ.get("SIDEQUEST_OTLP_ENDPOINT")
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        # Tighter flush than the SDK default (5 s, 512 batch) so spans
        # land in Jaeger within ~2 s of close — during gameplay a 5 s
        # window can hide whether the bridge is firing for the current
        # turn, which exactly defeats the point of using Jaeger as a
        # live observability tool.
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True),
                schedule_delay_millis=2000,
                max_export_batch_size=128,
                export_timeout_millis=5000,
            )
        )
        logger.info("otel.otlp_exporter_registered endpoint=%s", otlp_endpoint)

        # The OTLP exporter only catches spans started via
        # ``tracer().start_as_current_span(...)``. Watcher-published
        # semantic events (NPC reinventions, render gaps, confrontation
        # triggers, ...) reach the exporter only when the
        # publish_event→span bridge is also enabled. Without it the
        # operator gets a half-wired Jaeger trail that hides exactly
        # the events the GM panel exists to surface — exactly the
        # silent-fallback failure CLAUDE.md's OTEL Observability
        # Principle was written to prevent. Warn loudly at startup.
        if os.environ.get("SIDEQUEST_WATCHER_AS_SPANS") != "1":
            logger.warning(
                "otel.watcher_bridge_disabled — OTLP exporter is on but "
                "SIDEQUEST_WATCHER_AS_SPANS is unset; semantic watcher events "
                "(publish_event calls) will NOT reach Jaeger. "
                "Set SIDEQUEST_WATCHER_AS_SPANS=1 for the full semantic stream."
            )
    else:
        # Loud-fail when no OTLP endpoint is configured. Without this
        # the operator can't distinguish "nothing interesting happened"
        # from "the wire was never plugged in" by reading the server
        # log — and that ambiguity is the bug Story 45-41 fixes.
        logger.warning(
            "otel.otlp_dormant — SIDEQUEST_OTLP_ENDPOINT is unset; no spans "
            "will leave this process. Set SIDEQUEST_OTLP_ENDPOINT=host:port "
            "(e.g. localhost:4317) to flow traces to a collector like Jaeger."
        )

    trace.set_tracer_provider(provider)

    _initialized = True


def tracer() -> trace.Tracer:
    """Return the sidequest-server tracer."""
    return trace.get_tracer("sidequest-server")
