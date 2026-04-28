"""Turn pipeline spans — root ``turn`` span and its phase children."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_TURN = "turn"
SPAN_TURN_BARRIER = "turn.barrier"
SPAN_TURN_STATE_UPDATE = "turn.state_update"
SPAN_TURN_SYSTEM_TICK = "turn.system_tick"
SPAN_TURN_SYSTEM_TICK_TROPES = "turn.system_tick.tropes"
SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT = "turn.system_tick.beat_context"
SPAN_TURN_MEDIA = "turn.media"
SPAN_TURN_TROPES = "turn.tropes"
SPAN_TURN_PHASE_TRANSITION = "turn.phase_transition"
SPAN_TURN_SLASH_COMMAND = "turn.slash_command"
SPAN_TURN_PREPROCESS_LLM = "turn.preprocess.llm"
SPAN_TURN_PREPROCESS_PARSE = "turn.preprocess.parse"
SPAN_TURN_PREPROCESS_WISH_CHECK = "turn.preprocess.wish_check"
SPAN_TURN_ASSEMBLE = "turn.assemble"

FLAT_ONLY_SPANS.update({
    SPAN_TURN,
    SPAN_TURN_BARRIER,
    SPAN_TURN_STATE_UPDATE,
    SPAN_TURN_SYSTEM_TICK,
    SPAN_TURN_SYSTEM_TICK_TROPES,
    SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT,
    SPAN_TURN_MEDIA,
    SPAN_TURN_TROPES,
    SPAN_TURN_PHASE_TRANSITION,
    SPAN_TURN_SLASH_COMMAND,
    SPAN_TURN_PREPROCESS_LLM,
    SPAN_TURN_PREPROCESS_PARSE,
    SPAN_TURN_PREPROCESS_WISH_CHECK,
    SPAN_TURN_ASSEMBLE,
})

# ---------------------------------------------------------------------------
# Story 45-11 — turn_manager.round invariant span.
#
# Emitted at the end of every narration tick (after ``append_narrative``).
# Lie-detector for the GM panel (Sebastien-tier mechanical visibility):
# Felix's Playtest 3 ended round=65 / max(narrative_log)=72; the divergence
# was silent because no subsystem watched it. With this span on every tick,
# the dashboard can chart ``round`` vs ``max_narrative_round`` and colour
# any non-zero ``gap`` as a violation row.
# ---------------------------------------------------------------------------

SPAN_TURN_MANAGER_ROUND_INVARIANT = "turn_manager.round_invariant"
SPAN_ROUTES[SPAN_TURN_MANAGER_ROUND_INVARIANT] = SpanRoute(
    event_type="state_transition",
    component="turn_manager",
    extract=lambda span: {
        "field": "round_invariant",
        "round": (span.attributes or {}).get("round", 0),
        "interaction": (span.attributes or {}).get("interaction", 0),
        "max_narrative_round": (span.attributes or {}).get(
            "max_narrative_round", 0
        ),
        "gap": (span.attributes or {}).get("gap", 0),
        "holds": (span.attributes or {}).get("holds", True),
        "divergence_direction": (span.attributes or {}).get(
            "divergence_direction", "sync"
        ),
    },
)


@contextmanager
def round_invariant_span(
    *,
    round: int,  # noqa: A002 — match the snapshot field name on the GM panel
    interaction: int,
    max_narrative_round: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``turn_manager.round_invariant`` span.

    Story 45-11 AC4: emitted at the end of every narration tick with
    ``round`` / ``interaction`` / ``max_narrative_round`` plus the derived
    ``gap`` / ``holds`` / ``divergence_direction`` (lagging|ahead|sync).
    The dashboard's typed Subsystems tab reads ``gap`` and ``holds`` from
    the routed ``state_transition`` event; the firehose
    ``agent_span_close`` carries the same attributes for raw timeline use.
    """
    gap = max_narrative_round - round
    holds = gap == 0
    if gap > 0:
        direction = "lagging"
    elif gap < 0:
        direction = "ahead"
    else:
        direction = "sync"
    attributes: dict[str, Any] = {
        "round": round,
        "interaction": interaction,
        "max_narrative_round": max_narrative_round,
        "gap": gap,
        "holds": holds,
        "divergence_direction": direction,
        **attrs,
    }
    with Span.open(
        SPAN_TURN_MANAGER_ROUND_INVARIANT,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def turn_span(
    *,
    turn_id: int,
    player_id: str,
    agent_name: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the root ``turn`` span. Required attributes match ADR-031 §"Layer 2"."""
    with Span.open(
        SPAN_TURN,
        {"turn_id": turn_id, "player_id": player_id, "agent_name": agent_name, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
