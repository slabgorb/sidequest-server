"""Tests for the narration.turn rollup OTEL span."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.telemetry.spans.cost import narration_turn_cost_span


def _fresh_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a TracerProvider backed by an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _local_tracer(provider: TracerProvider) -> trace.Tracer:
    """Get a tracer scoped to a specific provider (avoids global provider lock)."""
    return provider.get_tracer("test")


def test_span_name_is_narration_turn() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with narration_turn_cost_span(
        world_id="w",
        session_id="s",
        turn_number=42,
        acting_pc="alex",
        _tracer=t,
    ):
        pass
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "narration.turn"


def test_seed_attributes_present() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with narration_turn_cost_span(
        world_id="seaboard",
        session_id="sat-night",
        turn_number=42,
        acting_pc="alex",
        _tracer=t,
    ):
        pass
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["world_id"] == "seaboard"
    assert attrs["session_id"] == "sat-night"
    assert attrs["turn_number"] == 42
    assert attrs["acting_pc"] == "alex"


def test_rollup_attributes_set_by_caller() -> None:
    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with narration_turn_cost_span(
        world_id="w", session_id="s", turn_number=1, acting_pc="alex", _tracer=t
    ) as span:
        span.set_attributes(
            {
                "narration.turn.model_chosen": "claude-sonnet-4-6",
                "narration.turn.total_input_tokens": 5000,
                "narration.turn.total_output_tokens": 1200,
                "narration.turn.cache_read_tokens": 12000,
                "narration.turn.cache_write_tokens": 0,
                "narration.turn.total_cost_usd": 0.067,
                "narration.turn.tool_call_count": 3,
                "narration.turn.llm_request_count": 4,
            }
        )
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["narration.turn.total_cost_usd"] == pytest.approx(0.067)
    assert attrs["narration.turn.tool_call_count"] == 3
