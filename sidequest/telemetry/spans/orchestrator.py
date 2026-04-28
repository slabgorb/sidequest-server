"""Orchestrator spans — process_action root + injection phases."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_ORCHESTRATOR_PROCESS_ACTION = "orchestrator.process_action"
SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET = "orchestrator.narrator_session_reset"
SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION = "orchestrator.genre_identity_injection"
SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION = "orchestrator.tactical_grid_injection"
SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION = "orchestrator.trope_beat_injection"
SPAN_ORCHESTRATOR_PARTY_PEER_INJECTION = "orchestrator.party_peer_injection"
SPAN_ORCHESTRATOR_LORE_FILTER = "orchestrator.lore_filter"
SPAN_ORCHESTRATOR_NOTORIOUS_PARTY_GATE = "orchestrator.notorious_party_gate"

FLAT_ONLY_SPANS.update({
    SPAN_ORCHESTRATOR_PROCESS_ACTION,
    SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET,
    SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION,
    SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION,
    SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION,
    SPAN_ORCHESTRATOR_PARTY_PEER_INJECTION,
    SPAN_ORCHESTRATOR_LORE_FILTER,
    SPAN_ORCHESTRATOR_NOTORIOUS_PARTY_GATE,
})


@contextmanager
def orchestrator_process_action_span(
    action_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_ORCHESTRATOR_PROCESS_ACTION,
        {"action_len": action_len, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def orchestrator_notorious_party_gate_span(
    *,
    player_count: int,
    notorious_party_gated: bool,
    party_context_available: bool,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Story 45-8 — fires every turn carrying the gate decision.

    Attributes:
        session.player_count       (int)   actual playing-player count
        notorious_party_gated      (bool)  True iff the gate dropped peers
        party_context_available    (bool)  True iff peers reach the prompt
    """
    with Span.open(
        SPAN_ORCHESTRATOR_NOTORIOUS_PARTY_GATE,
        {
            "session.player_count": player_count,
            "notorious_party_gated": notorious_party_gated,
            "party_context_available": party_context_available,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span
