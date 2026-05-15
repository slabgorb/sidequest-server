"""Tests for the tool.{category}.{name} OTEL span."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.agents.tool_registry import ToolCategory
from sidequest.telemetry.spans.tool_dispatch import tool_dispatch_span


@pytest.fixture
def exporter() -> Generator[InMemorySpanExporter, None, None]:
    """Attach an in-memory exporter to the live singleton provider.

    Deviation from plan: ``trace.set_tracer_provider`` can only be called
    once per process (OTel logs a warning and silently ignores subsequent
    calls), so the plan's fixture pattern works only for the first test.
    Instead we follow the project-idiomatic ``otel_capture`` fixture pattern
    (tests/agents/conftest.py): call ``init_tracer()`` to ensure the
    singleton is initialised, then add a ``SimpleSpanProcessor`` to the
    existing provider.  The test body assertions are unchanged.
    """
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exp = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exp)
    provider.add_span_processor(processor)
    yield exp
    processor.shutdown()


def test_read_span_name(exporter: InMemorySpanExporter) -> None:
    with tool_dispatch_span(name="query_npc", category=ToolCategory.READ):
        pass
    assert exporter.get_finished_spans()[0].name == "tool.read.query_npc"


def test_write_span_name(exporter: InMemorySpanExporter) -> None:
    with tool_dispatch_span(name="apply_damage", category=ToolCategory.WRITE):
        pass
    assert exporter.get_finished_spans()[0].name == "tool.write.apply_damage"


def test_generate_span_name(exporter: InMemorySpanExporter) -> None:
    with tool_dispatch_span(name="roll_dice", category=ToolCategory.GENERATE):
        pass
    assert exporter.get_finished_spans()[0].name == "tool.gen.roll_dice"


def test_seed_attributes(exporter: InMemorySpanExporter) -> None:
    with tool_dispatch_span(
        name="query_npc",
        category=ToolCategory.READ,
        perspective_pc="alex",
    ) as span:
        span.set_attribute("tool.npc.name", "innkeeper")
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["tool.name"] == "query_npc"
    assert attrs["tool.category"] == "read"
    assert attrs["tool.perspective_pc"] == "alex"
    assert attrs["tool.npc.name"] == "innkeeper"


def test_records_exception(exporter: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError), tool_dispatch_span(name="x", category=ToolCategory.READ):
        raise RuntimeError("boom")
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"


def test_no_perspective_pc_attr_when_none(exporter: InMemorySpanExporter) -> None:
    """When perspective_pc is None (e.g. pre-PC-selection), the attr is omitted."""
    with tool_dispatch_span(name="query_npc", category=ToolCategory.READ):
        pass
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert "tool.perspective_pc" not in attrs
