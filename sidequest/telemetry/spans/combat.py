"""Combat lifecycle spans — tick, end, player death."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_COMBAT_TICK = "combat.tick"
SPAN_ROUTES[SPAN_COMBAT_TICK] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.tick",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "beat": (span.attributes or {}).get("beat", 0),
        "phase": (span.attributes or {}).get("phase", ""),
    },
)
SPAN_COMBAT_ENDED = "combat.ended"
SPAN_ROUTES[SPAN_COMBAT_ENDED] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.ended",
        "outcome": (span.attributes or {}).get("outcome", ""),
        "duration_beats": (span.attributes or {}).get("duration_beats", 0),
    },
)
SPAN_COMBAT_PLAYER_DEAD = "combat.player_dead"
SPAN_ROUTES[SPAN_COMBAT_PLAYER_DEAD] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.player_dead",
        "player_name": (span.attributes or {}).get("player_name", ""),
    },
)


@contextmanager
def combat_tick_span(
    *,
    encounter_type: str,
    beat: int,
    phase: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COMBAT_TICK,
        {"encounter_type": encounter_type, "beat": beat, "phase": phase, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def combat_ended_span(
    *,
    outcome: str,
    duration_beats: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COMBAT_ENDED,
        {"outcome": outcome, "duration_beats": duration_beats, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def combat_player_dead_span(
    *,
    player_name: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COMBAT_PLAYER_DEAD,
        {"player_name": player_name, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
