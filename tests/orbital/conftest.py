"""Orbital test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture
def otel_capture():
    """Capture spans emitted to the live OTEL tracer provider singleton.

    Mirrors the pattern in ``tests/server/conftest.py`` and
    ``tests/agents/conftest.py``: ``tracer()`` inside our span context
    managers closes over the global provider, so installing a
    ``SimpleSpanProcessor`` on the live singleton is the reliable way
    to observe spans emitted by production code paths.
    """
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
