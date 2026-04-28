"""Turn pipeline spans — root ``turn`` span and its phase children."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
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
