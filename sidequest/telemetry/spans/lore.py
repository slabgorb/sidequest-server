"""Lore spans — narrator-established canonical statements."""

from __future__ import annotations

import json as _json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_LORE_ESTABLISHED = "lore.established"
SPAN_ROUTES[SPAN_LORE_ESTABLISHED] = SpanRoute(
    event_type="lore_retrieval",
    component="lore",
    extract=lambda span: {
        "field": "lore_established",
        "op": "appended",
        "reason": "narrator_established",
        "items": (span.attributes or {}).get("items_json", "[]"),
        "added_count": (span.attributes or {}).get("added_count", 0),
        "total": (span.attributes or {}).get("total", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)


@contextmanager
def lore_established_span(
    *,
    items: list[str],
    added_count: int,
    total: int,
    player_name: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """``items`` is JSON-encoded — OTEL silently drops list attribute values."""
    attributes: dict[str, Any] = {
        "items_json": _json.dumps(list(items)),
        "added_count": added_count,
        "total": total,
        "player_name": player_name,
        "turn_number": turn_number,
        **attrs,
    }
    with Span.open(SPAN_LORE_ESTABLISHED, attributes, tracer_override=_tracer) as span:
        yield span
