"""Tests for the llm.request OTEL span."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.telemetry.spans.llm_request import llm_request_span


def _fresh_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a TracerProvider backed by an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _local_tracer(provider: TracerProvider) -> trace.Tracer:
    """Get a tracer scoped to a specific provider (avoids global provider lock)."""
    return provider.get_tracer("test")


def test_span_name_is_llm_request() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with llm_request_span(model="claude-sonnet-4-6", _tracer=t):
        pass
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "llm.request"


def test_span_carries_input_attributes() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with llm_request_span(model="claude-sonnet-4-6", _tracer=t) as span:
        span.set_attributes(
            {
                "llm.input_tokens": 100,
                "llm.output_tokens": 50,
                "llm.cached_input_read_tokens": 80,
                "llm.cached_input_write_tokens": 0,
                "llm.stop_reason": "end_turn",
                "llm.cost_usd": 0.0042,
                "llm.ratelimit_input_tokens_remaining": 100000,
            }
        )
    spans = exporter.get_finished_spans()
    attrs = dict(spans[0].attributes or {})
    assert attrs["llm.model"] == "claude-sonnet-4-6"
    assert attrs["llm.input_tokens"] == 100
    assert attrs["llm.cached_input_read_tokens"] == 80
    assert attrs["llm.cost_usd"] == pytest.approx(0.0042)


def test_span_records_exception() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with pytest.raises(RuntimeError), llm_request_span(model="claude-sonnet-4-6", _tracer=t):
        raise RuntimeError("boom")
    spans = exporter.get_finished_spans()
    assert spans[0].status.status_code.name == "ERROR"
