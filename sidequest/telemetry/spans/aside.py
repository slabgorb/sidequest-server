"""Aside spans — out-of-band player→GM table-talk (ADR-107).

``aside.resolve`` fires on every aside resolution so Sebastien's GM panel
(CLAUDE.md "OTEL Observability Principle") proves the channel engaged and
exposes whether the answer was grounded. Routed (not flat-only) because an
ungrounded aside is exactly the kind of narrator-lie the panel must catch.
"""

from __future__ import annotations

from ._core import SPAN_ROUTES, SpanRoute

SPAN_ASIDE_RESOLVE = "aside.resolve"

SPAN_ROUTES[SPAN_ASIDE_RESOLVE] = SpanRoute(
    event_type="state_transition",
    component="aside",
    extract=lambda span: {
        "field": "aside",
        "op": "resolved",
        "asker_id": (span.attributes or {}).get("asker_id", ""),
        "outcome": (span.attributes or {}).get("outcome", ""),
        "grounded_on": (span.attributes or {}).get("grounded_on", ""),
        "model": (span.attributes or {}).get("model", ""),
        "latency_ms": (span.attributes or {}).get("latency_ms", 0),
    },
)
