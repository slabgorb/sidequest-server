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
        "chapters_added": list((span.attributes or {}).get("chapters_added", [])),
    },
)

# ---------------------------------------------------------------------------
# Story 45-23 — chapter-promotion writeback spans.
#
# Closes Felix's Playtest 3 gap: 71 turns of dense play, ``narrative_log``
# and ``lore_store`` empty of arc-sourced content because the chapter-
# promotion path never wrote back. Three spans pin the pipeline so the
# GM panel can see exactly which step engaged on each promotion turn.
# ---------------------------------------------------------------------------

SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED = "world_history.arc_embedding_seed"
SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK = "world_history.narrative_log_writeback"
SPAN_WORLD_HISTORY_LORE_WRITEBACK = "world_history.lore_writeback"

SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED] = SpanRoute(
    event_type="state_transition",
    component="world_history",
    extract=lambda span: {
        "field": "arc_embedding_seed",
        "chapter_id": (span.attributes or {}).get("chapter_id", ""),
        "narrative_entries_appended": (span.attributes or {}).get("narrative_entries_appended", 0),
        "lore_fragments_minted": (span.attributes or {}).get("lore_fragments_minted", 0),
        "lore_fragments_skipped_duplicate": (span.attributes or {}).get(
            "lore_fragments_skipped_duplicate", 0
        ),
        "content_bytes_seeded": (span.attributes or {}).get("content_bytes_seeded", 0),
        "interaction": (span.attributes or {}).get("interaction", 0),
    },
)

SPAN_ROUTES[SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK] = SpanRoute(
    event_type="state_transition",
    component="world_history",
    extract=lambda span: {
        "field": "narrative_log_writeback",
        "chapter_id": (span.attributes or {}).get("chapter_id", ""),
        "entries_count": (span.attributes or {}).get("entries_count", 0),
        "interaction": (span.attributes or {}).get("interaction", 0),
        "entry_type": (span.attributes or {}).get("entry_type", ""),
    },
)

SPAN_ROUTES[SPAN_WORLD_HISTORY_LORE_WRITEBACK] = SpanRoute(
    event_type="state_transition",
    component="world_history",
    extract=lambda span: {
        "field": "lore_writeback",
        "chapter_id": (span.attributes or {}).get("chapter_id", ""),
        "fragment_id": (span.attributes or {}).get("fragment_id", ""),
        "category": (span.attributes or {}).get("category", ""),
        "content_bytes": (span.attributes or {}).get("content_bytes", 0),
        "pending_embedding": (span.attributes or {}).get("pending_embedding", False),
    },
)
