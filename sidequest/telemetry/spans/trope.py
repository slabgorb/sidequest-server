"""Trope engine spans — tick + activation/resolution + resolution handshake.

Story 45-20 promoted ``SPAN_TROPE_RESOLVE`` out of ``FLAT_ONLY_SPANS`` into
``SPAN_ROUTES`` so the typed Subsystems feed sees existing trope-resolution
events alongside the new handshake. The handshake itself fires from
``_handshake_resolved_tropes`` (sidequest/server/narration_apply.py) and
documents the durable-record write that updates ``quest_log`` /
``active_stakes`` when a trope's status flips into ``"resolved"``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_TROPE_TICK = "trope_tick"
SPAN_TROPE_TICK_PER = "trope.tick"
SPAN_TROPE_ROOM_TICK = "trope.room_tick"
SPAN_TROPE_ACTIVATE = "trope_activate"
SPAN_TROPE_RESOLVE = "trope_resolve"
SPAN_TROPE_CROSS_SESSION = "trope.cross_session"
SPAN_TROPE_EVALUATE_TRIGGERS = "trope.evaluate_triggers"
SPAN_TROPE_RESOLUTION_HANDSHAKE = "trope.resolution_handshake"

FLAT_ONLY_SPANS.update(
    {
        SPAN_TROPE_TICK,
        SPAN_TROPE_TICK_PER,
        SPAN_TROPE_ROOM_TICK,
        SPAN_TROPE_ACTIVATE,
        SPAN_TROPE_CROSS_SESSION,
        SPAN_TROPE_EVALUATE_TRIGGERS,
    }
)

# Story 45-20 — promoted out of FLAT_ONLY_SPANS so the GM panel's typed
# state_transition feed surfaces trope resolution events.
SPAN_ROUTES[SPAN_TROPE_RESOLVE] = SpanRoute(
    event_type="state_transition",
    component="trope",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "interaction": (span.attributes or {}).get("interaction", 0),
        "genre_slug": (span.attributes or {}).get("genre_slug", ""),
    },
)

# Story 45-20 — handshake span fires on every detection of a resolved
# trope at the post-record_interaction site, INCLUDING idempotent
# re-detects (no double-write but the panel sees the path engaged).
SPAN_ROUTES[SPAN_TROPE_RESOLUTION_HANDSHAKE] = SpanRoute(
    event_type="state_transition",
    component="trope",
    extract=lambda span: {
        "field": "quest_log",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "prior_status": (span.attributes or {}).get("prior_status", ""),
        "new_status": (span.attributes or {}).get("new_status", ""),
        "interaction": (span.attributes or {}).get("interaction", 0),
        "quest_log_key": (span.attributes or {}).get("quest_log_key", ""),
        "active_stakes_appended": (span.attributes or {}).get("active_stakes_appended", False),
        "source": (span.attributes or {}).get("source", ""),
    },
)


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


@contextmanager
def trope_resolution_handshake_span(
    *,
    trope_id: str,
    prior_status: str,
    new_status: str,
    interaction: int,
    quest_log_key: str,
    active_stakes_appended: bool,
    source: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Emit one ``trope.resolution_handshake`` span per detected resolution.

    Fires for every trope whose current status is ``"resolved"``, even when
    the prior status was already ``"resolved"`` (idempotent re-detect:
    ``active_stakes_appended=False``). The lie-detector signal: the panel
    needs the path-engaged record even on no-op turns.
    """

    attributes: dict[str, Any] = {
        "trope_id": trope_id,
        "prior_status": prior_status,
        "new_status": new_status,
        "interaction": interaction,
        "quest_log_key": quest_log_key,
        "active_stakes_appended": active_stakes_appended,
        "source": source,
        **attrs,
    }
    with Span.open(
        SPAN_TROPE_RESOLUTION_HANDSHAKE,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
