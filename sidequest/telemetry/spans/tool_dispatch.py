"""OTEL span: tool.{read,write,gen}.{name} — one span per tool handler call.

Standard attributes (set by dispatcher):
    tool.name              str
    tool.category          "read" | "write" | "generate"
    tool.perspective_pc    str | None
    tool.result_status     "ok" | "not_found" | "error_recoverable" | "error_fatal"
    tool.result_size_bytes int
    (duration computed by the OTEL collector from span start/end timestamps;
     not a span attribute)

Per-tool typed attributes use `tool.<short_name>.*` namespace (e.g.
`tool.npc.name`, `tool.damage.hp_delta`). Phase C tools set their own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry.trace import Span, Status, StatusCode

from sidequest.agents.tool_registry import ToolCategory
from sidequest.telemetry.spans.span import Span as SpanHelper

_CATEGORY_PREFIX: dict[ToolCategory, str] = {
    ToolCategory.READ: "tool.read",
    ToolCategory.WRITE: "tool.write",
    ToolCategory.GENERATE: "tool.gen",
}


@contextmanager
def tool_dispatch_span(
    *,
    name: str,
    category: ToolCategory,
    perspective_pc: str | None = None,
) -> Iterator[Span]:
    """Open a tool.{cat}.{name} span seeded with standard tool attributes."""
    span_name = f"{_CATEGORY_PREFIX[category]}.{name}"
    seed_attrs: dict[str, str] = {
        "tool.name": name,
        "tool.category": category.value,
    }
    if perspective_pc is not None:
        seed_attrs["tool.perspective_pc"] = perspective_pc
    with SpanHelper.open(span_name, seed_attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
