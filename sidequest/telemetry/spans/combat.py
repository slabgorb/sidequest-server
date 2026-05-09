"""Combat lifecycle spans — tick, end, player death, morale check."""

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
# C&C B/X Task 12 — morale check span so the GM panel can verify B/X morale
# rolls are wired and not silently suppressed (CLAUDE.md lie-detector discipline).
SPAN_MORALE_CHECK = "confrontation.morale_check"
SPAN_ROUTES[SPAN_MORALE_CHECK] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "morale_check",
        "trigger": (span.attributes or {}).get("trigger", ""),
        "score": (span.attributes or {}).get("score", 0),
        "roll": (span.attributes or {}).get("roll", ""),
        "total": (span.attributes or {}).get("total", 0),
        "outcome": (span.attributes or {}).get("outcome", ""),
        "opponent_side_label": (span.attributes or {}).get("opponent_side_label", ""),
        "mindless_opponents_count": (span.attributes or {}).get("mindless_opponents_count", 0),
        "flee_consequence": (span.attributes or {}).get("flee_consequence", ""),
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
