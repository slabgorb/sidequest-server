"""Trope engine spans — tick + activation/resolution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_TROPE_TICK = "trope_tick"
SPAN_TROPE_TICK_PER = "trope.tick"
SPAN_TROPE_ROOM_TICK = "trope.room_tick"
SPAN_TROPE_ACTIVATE = "trope_activate"
SPAN_TROPE_RESOLVE = "trope_resolve"
SPAN_TROPE_CROSS_SESSION = "trope.cross_session"
SPAN_TROPE_EVALUATE_TRIGGERS = "trope.evaluate_triggers"

FLAT_ONLY_SPANS.update({
    SPAN_TROPE_TICK,
    SPAN_TROPE_TICK_PER,
    SPAN_TROPE_ROOM_TICK,
    SPAN_TROPE_ACTIVATE,
    SPAN_TROPE_RESOLVE,
    SPAN_TROPE_CROSS_SESSION,
    SPAN_TROPE_EVALUATE_TRIGGERS,
})


@contextmanager
def trope_tick_span(
    trope_count: int,
    multiplier: float,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_TROPE_TICK,
        {"trope_count": trope_count, "multiplier": multiplier, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
