"""Room-state spans — per-room container retrieval lifecycle (Story 45-13).

Three spans cover the wire:

- ``container.retrieval_recorded`` — fires when ``narration_apply.py``
  marks a container as retrieved for the first time in the current
  room. Audit attributes carry ``room_id``, ``container_id``, and
  ``round_number`` so the GM panel can surface the lifecycle event.

- ``container.retrieval_blocked`` — fires when the apply-time negative
  gate filters a duplicate retrieval. Carries ``prior_retrieved_at_round``
  and ``current_round`` so Sebastien (lie-detector audience) can see
  the bug-prevention firing live, not just infer it from missing items.

- ``room.state_injected`` — fires every time the prompt-build seam
  (``session_helpers._build_turn_context``) reads the current room's
  ``RoomState``. The no-op-firing case (``retrieved_container_count=0``)
  is load-bearing: without it, the GM panel can't tell whether the
  gate machinery is engaged or whether the system isn't bothering to
  look.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

# ---------------------------------------------------------------------------
# Span name constants
# ---------------------------------------------------------------------------

SPAN_CONTAINER_RETRIEVAL_RECORDED = "container.retrieval_recorded"
SPAN_CONTAINER_RETRIEVAL_BLOCKED = "container.retrieval_blocked"
SPAN_ROOM_STATE_INJECTED = "room.state_injected"


# ---------------------------------------------------------------------------
# SPAN_ROUTES — surface the lifecycle as ``state_transition`` events under
# ``component=room_state`` so the GM panel renders them on the same lane.
# ---------------------------------------------------------------------------

SPAN_ROUTES[SPAN_CONTAINER_RETRIEVAL_RECORDED] = SpanRoute(
    event_type="state_transition",
    component="room_state",
    extract=lambda span: {
        "field": "room_state",
        "op": "container_retrieval_recorded",
        "room_id": (span.attributes or {}).get("room_id", ""),
        "container_id": (span.attributes or {}).get("container_id", ""),
        "round_number": (span.attributes or {}).get("round_number", 0),
        "items_gained_count": (span.attributes or {}).get("items_gained_count", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
    },
)

SPAN_ROUTES[SPAN_CONTAINER_RETRIEVAL_BLOCKED] = SpanRoute(
    event_type="state_transition",
    component="room_state",
    extract=lambda span: {
        "field": "room_state",
        "op": "container_retrieval_blocked",
        "room_id": (span.attributes or {}).get("room_id", ""),
        "container_id": (span.attributes or {}).get("container_id", ""),
        "prior_retrieved_at_round": (span.attributes or {}).get(
            "prior_retrieved_at_round",
            0,
        ),
        "current_round": (span.attributes or {}).get("current_round", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
    },
)

SPAN_ROUTES[SPAN_ROOM_STATE_INJECTED] = SpanRoute(
    event_type="state_transition",
    component="room_state",
    extract=lambda span: {
        "field": "room_state",
        "op": "room_state_injected",
        "room_id": (span.attributes or {}).get("room_id", ""),
        "retrieved_container_count": (span.attributes or {}).get(
            "retrieved_container_count",
            0,
        ),
        "interaction": (span.attributes or {}).get("interaction", 0),
    },
)


# ---------------------------------------------------------------------------
# Helper context managers — mirror the inventory.py pattern (kwargs-only,
# late-bound tracer for in-memory exporter installation under test).
# ---------------------------------------------------------------------------


@contextmanager
def container_retrieval_recorded_span(
    *,
    room_id: str,
    container_id: str,
    round_number: int,
    interaction: int,
    items_gained_count: int,
    player_name: str,
    genre: str = "",
    world: str = "",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """First retrieval of a container in a room — load-bearing audit
    fields live here per the OTEL contract."""
    attributes: dict[str, Any] = {
        "room_id": room_id,
        "container_id": container_id,
        "round_number": round_number,
        "interaction": interaction,
        "items_gained_count": items_gained_count,
        "player_name": player_name,
        "genre": genre,
        "world": world,
        **attrs,
    }
    with Span.open(
        SPAN_CONTAINER_RETRIEVAL_RECORDED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def container_retrieval_blocked_span(
    *,
    room_id: str,
    container_id: str,
    prior_retrieved_at_round: int,
    current_round: int,
    interaction: int,
    player_name: str,
    genre: str = "",
    world: str = "",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Apply-time negative gate firing — the load-bearing block per AC #6.
    The narrator nonetheless emitted a duplicate retrieval; the gate
    filtered it. ``prior_retrieved_at_round`` and ``current_round`` are
    the audit fields the GM panel surfaces."""
    attributes: dict[str, Any] = {
        "room_id": room_id,
        "container_id": container_id,
        "prior_retrieved_at_round": prior_retrieved_at_round,
        "current_round": current_round,
        "interaction": interaction,
        "player_name": player_name,
        "genre": genre,
        "world": world,
        **attrs,
    }
    with Span.open(
        SPAN_CONTAINER_RETRIEVAL_BLOCKED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def room_state_injected_span(
    *,
    room_id: str,
    retrieved_container_count: int,
    interaction: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Prompt-build seam — fires on EVERY narrator turn including the
    no-prior-retrievals case (``retrieved_container_count=0``).
    Sebastien's lie-detector requires the no-op-firing case so the GM
    panel can distinguish "gate engaged with nothing to report" from
    "gate not engaged at all"."""
    attributes: dict[str, Any] = {
        "room_id": room_id,
        "retrieved_container_count": retrieved_container_count,
        "interaction": interaction,
        **attrs,
    }
    with Span.open(
        SPAN_ROOM_STATE_INJECTED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
