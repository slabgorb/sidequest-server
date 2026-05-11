"""Recent-narrative spans — Recency-zone narrative window (Story 49-1).

ADR-098 dropped ``claude -p --resume`` and the recent-narration block
went with it; the narrator started losing prior-turn details because
``narrative_log`` lived in the Valley-zone ``<game_state>`` JSON dump.

``recent_narrative_context_injected`` fires on EVERY narrator turn —
including the empty-log case (``turn_count=0``) — so the GM panel can
distinguish "Recency injector engaged with nothing to inject" from
"injector not wired at all" (Sebastien's lie-detector contract,
matches the ``room.state_injected`` no-op-fire discipline).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_RECENT_NARRATIVE_CONTEXT_INJECTED = "recent_narrative_context_injected"


SPAN_ROUTES[SPAN_RECENT_NARRATIVE_CONTEXT_INJECTED] = SpanRoute(
    event_type="state_transition",
    component="prompt_builder",
    extract=lambda span: {
        "field": "recent_narrative_context",
        "op": "injected",
        "turn_count": (span.attributes or {}).get("turn_count", 0),
        "total_tokens": (span.attributes or {}).get("total_tokens", 0),
    },
)


@contextmanager
def recent_narrative_context_injected_span(
    *,
    turn_count: int,
    total_tokens: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Recency-zone narrative-window injection — fires on every narrator
    turn so the GM panel can audit whether the recency-injector engaged."""
    attributes: dict[str, Any] = {
        "turn_count": turn_count,
        "total_tokens": total_tokens,
        **attrs,
    }
    with Span.open(
        SPAN_RECENT_NARRATIVE_CONTEXT_INJECTED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
