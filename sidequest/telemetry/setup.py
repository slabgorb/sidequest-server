"""OpenTelemetry tracer setup for sidequest-server.

The default destination for spans is the WatcherSpanProcessor (registered
in server/app.py). Console export is debug-only and gated behind
SIDEQUEST_OTEL_CONSOLE=1 so that normal runs don't pollute stdout with
span dumps.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

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

    trace.set_tracer_provider(provider)

    _initialized = True


def tracer() -> trace.Tracer:
    """Return the sidequest-server tracer."""
    return trace.get_tracer("sidequest-server")
