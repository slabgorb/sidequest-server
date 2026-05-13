"""Trope engine spans — tick + activation/resolution + tempo aggregate.

Story 45-20 promoted ``SPAN_TROPE_RESOLVE`` out of ``FLAT_ONLY_SPANS`` into
``SPAN_ROUTES`` so the typed Subsystems feed sees existing trope-resolution
events alongside the new handshake. The handshake itself fires from
``_handshake_resolved_tropes`` (sidequest/server/narration_apply.py).

Story 45-27 takes the next step: every trope-engine span now routes to
the GM panel's typed feed under ``component="tropes"`` so Sebastien's
lie-detector sees the engine's full per-turn behavior — tick deltas,
activations, cap refusals, cooldown refusals, and the per-turn aggregate
``turn.tropes`` carrying the three story-named metrics
(``active_trope_count``, ``progression_max``, ``progression_avg``).
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

# Story 50-4 — distinct from SPAN_TROPE_TICK_PER (which is rate_per_turn).
# Fires once per tick_tropes call where days_advanced > 0, regardless of
# whether any beats fired. Sebastien-axis: GM panel can distinguish
# in-session pacing drift from explicit time-skip drift.
SPAN_TROPE_TIME_SKIP = "trope.time_skip"

# Story 45-27 — diagnostic spans for activation refusals so the GM
# panel can chart "engine refused to activate this" distinctly from
# "engine never engaged".
SPAN_TROPE_CAP_BLOCKED = "trope.cap_blocked"
SPAN_TROPE_COOLDOWN_BLOCKED = "trope.cooldown_blocked"

# Spans that stay flat-only — these are diagnostic / dev-time only and
# do not need typed Subsystems-tab routing.
FLAT_ONLY_SPANS.update(
    {
        SPAN_TROPE_TICK,
        SPAN_TROPE_ROOM_TICK,
        SPAN_TROPE_CROSS_SESSION,
        SPAN_TROPE_EVALUATE_TRIGGERS,
    }
)


# Story 45-20 — resolved-trope durable record.
# Story 45-27 — extend extract to surface ``cooldown_until_turn`` so
# the GM panel can render the cooldown bar starting at resolution.
SPAN_ROUTES[SPAN_TROPE_RESOLVE] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "interaction": (span.attributes or {}).get("interaction", 0),
        "genre_slug": (span.attributes or {}).get("genre_slug", ""),
        "final_progress": (span.attributes or {}).get("final_progress", 0.0),
        "beats_fired_total": (span.attributes or {}).get("beats_fired_total", 0),
        "cooldown_until_turn": (span.attributes or {}).get("cooldown_until_turn", 0),
    },
)


# Story 45-20 — handshake span fires on every detection of a resolved
# trope at the post-record_interaction site.
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


# Story 45-27 — per-trope tick. The panel renders a per-trope
# progression sparkline alongside the aggregate so the GM can see
# *which* trope moved this turn, not just that something did.
SPAN_ROUTES[SPAN_TROPE_TICK_PER] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "progress_before": (span.attributes or {}).get("progress_before", 0.0),
        "progress_after": (span.attributes or {}).get("progress_after", 0.0),
        "delta": (span.attributes or {}).get("delta", 0.0),
        "accelerator_hits": (span.attributes or {}).get("accelerator_hits", 0),
        "decelerator_hits": (span.attributes or {}).get("decelerator_hits", 0),
    },
)


# Story 45-27 — dormant→progressing transition. ``cap_used`` lets the
# panel show "3 of 3 slots used" alongside the activation event.
SPAN_ROUTES[SPAN_TROPE_ACTIVATE] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "from_status": (span.attributes or {}).get("from_status", ""),
        "to_status": (span.attributes or {}).get("to_status", ""),
        "cap_used": (span.attributes or {}).get("cap_used", 0),
    },
)


# Story 45-27 — diagnostic: cap held back a candidate.
SPAN_ROUTES[SPAN_TROPE_CAP_BLOCKED] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "current_active_count": (span.attributes or {}).get("current_active_count", 0),
        "cap": (span.attributes or {}).get("cap", 0),
    },
)


# Story 45-27 — diagnostic: cooldown held back a candidate.
SPAN_ROUTES[SPAN_TROPE_COOLDOWN_BLOCKED] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "trope_id": (span.attributes or {}).get("trope_id", ""),
        "cooldown_until_turn": (span.attributes or {}).get("cooldown_until_turn", 0),
        "current_turn": (span.attributes or {}).get("current_turn", 0),
    },
)


# Story 50-4 — time-skip drift. Fires once per tick where days_advanced > 0,
# regardless of whether beats fired. The GM panel renders a +Nd badge and
# the per-trope advancement list so Sebastien can confirm the drift cap
# clamped correctly and which tropes (if any) skipped on a zero-rate def.
SPAN_ROUTES[SPAN_TROPE_TIME_SKIP] = SpanRoute(
    event_type="state_transition",
    component="tropes",
    extract=lambda span: {
        "field": "active_tropes",
        "days_requested": (span.attributes or {}).get("days_requested", 0),
        "days_applied": (span.attributes or {}).get("days_applied", 0),
        "clamped": (span.attributes or {}).get("clamped", False),
        "tropes_affected": list((span.attributes or {}).get("tropes_affected", ()) or ()),
        "tropes_skipped_zero_rate": list(
            (span.attributes or {}).get("tropes_skipped_zero_rate", ()) or ()
        ),
        "beats_fired_count": (span.attributes or {}).get("beats_fired_count", 0),
        "resolved_during_skip": list((span.attributes or {}).get("resolved_during_skip", ()) or ()),
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
    """Open the legacy aggregate span (kept for cross_session/room_tick).

    Story 45-27 introduces the new ``turn.tropes`` aggregate (helper in
    ``sidequest/telemetry/spans/turn.py``) — prefer that for per-turn
    tempo emission. This helper remains for the legacy room_tick /
    cross_session call sites that pre-date the per-turn aggregate.
    """
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
    """Emit one ``trope.resolution_handshake`` span per detected resolution."""

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
