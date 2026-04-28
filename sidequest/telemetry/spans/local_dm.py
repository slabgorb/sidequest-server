"""Local DM spans — decomposer + subsystem dispatch + lethality arbiter."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_LOCAL_DM_DECOMPOSE = "local_dm.decompose"
SPAN_ROUTES[SPAN_LOCAL_DM_DECOMPOSE] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.decompose",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "player_id": (span.attributes or {}).get("player_id", ""),
        "action_len": (span.attributes or {}).get("action_len", 0),
        "degraded": (span.attributes or {}).get("degraded", False),
        "degraded_reason": (span.attributes or {}).get("degraded_reason", ""),
    },
)
SPAN_LOCAL_DM_DISPATCH_BANK = "local_dm.dispatch_bank"
SPAN_ROUTES[SPAN_LOCAL_DM_DISPATCH_BANK] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.dispatch_bank",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "dispatch_count": (span.attributes or {}).get("dispatch_count", 0),
    },
)
SPAN_LOCAL_DM_SUBSYSTEM = "local_dm.subsystem"
SPAN_ROUTES[SPAN_LOCAL_DM_SUBSYSTEM] = SpanRoute(
    event_type="subsystem_exercise_summary",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.subsystem",
        "subsystem": (span.attributes or {}).get("subsystem", ""),
        "idempotency_key": (span.attributes or {}).get("idempotency_key", ""),
        "produced_directives": (span.attributes or {}).get("produced_directives", 0),
        "error": (span.attributes or {}).get("error", ""),
    },
)
SPAN_LOCAL_DM_LETHALITY_ARBITRATE = "local_dm.lethality_arbitrate"
SPAN_ROUTES[SPAN_LOCAL_DM_LETHALITY_ARBITRATE] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.lethality_arbitrate",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "genre_key": (span.attributes or {}).get("genre_key", ""),
        "verdict_count": (span.attributes or {}).get("verdict_count", 0),
    },
)


@contextmanager
def local_dm_decompose_span(
    turn_id: str,
    player_id: str,
    action_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Caller sets ``degraded`` / ``degraded_reason`` before return."""
    with Span.open(
        SPAN_LOCAL_DM_DECOMPOSE,
        {
            "turn_id": turn_id,
            "player_id": player_id,
            "action_len": action_len,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def local_dm_dispatch_bank_span(
    turn_id: str,
    dispatch_count: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_LOCAL_DM_DISPATCH_BANK,
        {"turn_id": turn_id, "dispatch_count": dispatch_count, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def local_dm_subsystem_span(
    subsystem: str,
    idempotency_key: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Caller records ``produced_directives`` on success or ``error`` on failure."""
    with Span.open(
        SPAN_LOCAL_DM_SUBSYSTEM,
        {"subsystem": subsystem, "idempotency_key": idempotency_key, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def lethality_arbitrate_span(
    turn_id: str,
    genre_key: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Caller sets ``verdict_count`` before return."""
    with Span.open(
        SPAN_LOCAL_DM_LETHALITY_ARBITRATE,
        {"turn_id": turn_id, "genre_key": genre_key, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
