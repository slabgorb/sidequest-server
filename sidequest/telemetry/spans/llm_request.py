"""OTEL span: llm.request — one span per API call to the Anthropic SDK.

Attributes (set by caller after the call returns):
    llm.model                              str
    llm.input_tokens                       int  (fresh, uncached)
    llm.output_tokens                      int
    llm.cached_input_read_tokens           int
    llm.cached_input_write_tokens          int
    llm.stop_reason                        str  (end_turn|max_tokens|tool_use|stop_sequence|error)
    llm.cost_usd                           float
    llm.ratelimit_input_tokens_remaining   int  (from anthropic-ratelimit-* headers)
    llm.iteration                          int  (1, 2, ... within a tool loop)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .span import Span


@contextmanager
def llm_request_span(
    *,
    model: str,
    iteration: int = 1,
    _tracer: trace.Tracer | None = None,
) -> Iterator[trace.Span]:
    """Open an llm.request span and seed model + iteration attributes."""
    with Span.open(
        "llm.request",
        {"llm.model": model, "llm.iteration": iteration},
        tracer_override=_tracer,
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
