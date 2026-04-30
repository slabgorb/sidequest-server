"""World-builder spans — materialization and arc recompute."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute

SPAN_WORLD_MATERIALIZED = "world.materialized"

FLAT_ONLY_SPANS.add(SPAN_WORLD_MATERIALIZED)

# ---------------------------------------------------------------------------
# Story 45-19 — world_history arc recompute spans.
#
# arc_tick fires on every recompute call (the "lie detector" Sebastien
# needs on the GM panel — a no-op tick is still observable). arc_promoted
# fires only when the maturity tier changes, scoped for filtered views
# of meaningful transitions.
# ---------------------------------------------------------------------------

SPAN_WORLD_HISTORY_ARC_TICK = "world_history.arc_tick"
SPAN_WORLD_HISTORY_ARC_PROMOTED = "world_history.arc_promoted"

SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_TICK] = SpanRoute(
    event_type="state_transition",
    component="world_history",
    extract=lambda span: {
        "field": "arc_tick",
        "interaction": (span.attributes or {}).get("interaction", 0),
        "round": (span.attributes or {}).get("round", 0),
        "from_maturity": (span.attributes or {}).get("from_maturity", ""),
        "to_maturity": (span.attributes or {}).get("to_maturity", ""),
        "chapters_before": (span.attributes or {}).get("chapters_before", 0),
        "chapters_after": (span.attributes or {}).get("chapters_after", 0),
        "tier_changed": (span.attributes or {}).get("tier_changed", False),
        "cadence_interval": (span.attributes or {}).get("cadence_interval", 0),
    },
)

SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_PROMOTED] = SpanRoute(
    event_type="state_transition",
    component="world_history",
    extract=lambda span: {
        "field": "arc_promoted",
        "interaction": (span.attributes or {}).get("interaction", 0),
        "from_maturity": (span.attributes or {}).get("from_maturity", ""),
        "to_maturity": (span.attributes or {}).get("to_maturity", ""),
        "chapters_added": list(
            (span.attributes or {}).get("chapters_added", [])
        ),
    },
)
