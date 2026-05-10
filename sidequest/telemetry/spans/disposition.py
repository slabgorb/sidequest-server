"""Disposition spans — NPC affinity shifts."""

from __future__ import annotations

from ._core import SPAN_ROUTES, SpanRoute

SPAN_DISPOSITION_SHIFT = "disposition.shift"

# Promoted from FLAT_ONLY (sprint 3 cold-subsystem audit). Without a typed
# event the GM panel could not show NPC affinity drift — narrator-described
# warming/cooling looked the same as engine-applied disposition changes.
SPAN_ROUTES[SPAN_DISPOSITION_SHIFT] = SpanRoute(
    event_type="state_transition",
    component="disposition",
    extract=lambda span: {
        "field": "disposition.shift",
        "npc_name": (span.attributes or {}).get("npc_name", ""),
        "delta": (span.attributes or {}).get("delta", 0),
        "before": (span.attributes or {}).get("before", 0),
        "after": (span.attributes or {}).get("after", 0),
    },
)
