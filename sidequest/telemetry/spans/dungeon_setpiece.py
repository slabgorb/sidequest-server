"""Dungeon set-piece attach spans (Beneath Sünden Plan 6 §6).

Only the trope.start span lives here now — it has a real caller in
start_trope_components (setpiece_attach.py). Subsequent spans
(setpiece.attach, quest.seed) are added as Tasks 4/3 ship their callers;
no stubs here (CLAUDE.md: No Stubbing).

The failure path emits trope.start with failed=True BEFORE re-raising so
the GM panel sees the content authoring bug rather than silence.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_TROPE_START = "trope.start"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_TROPE_START] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "active_tropes",
        "op": "trope_start",
        "trope_id": _attr("trope_id")(s),
        "setpiece_id": _attr("setpiece_id")(s),
        "origin_region_id": _attr("origin_region_id")(s),
        "failed": _attr("failed")(s),
    },
)


@contextmanager
def trope_start_span(
    *,
    trope_id: str,
    setpiece_id: str,
    origin_region_id: str,
    failed: bool = False,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open a trope.start span.

    Open before attempting resolution; set failed=True on the yielded span
    before raising when the trope_id is unknown — the GM panel must see
    the content bug, not silence.
    """
    with Span.open(
        SPAN_TROPE_START,
        {
            "trope_id": trope_id,
            "setpiece_id": setpiece_id,
            "origin_region_id": origin_region_id,
            "failed": failed,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_TROPE_START",
    "trope_start_span",
]
