"""Story 45-23 — the new arc-embedding writeback spans must be routed.

Three spans pin the chapter-promotion → embedding pipeline so the GM
panel can see exactly which step engaged on each promotion turn:

- ``world_history.arc_embedding_seed`` — fires once per promoted
  chapter (per context-story-45-23.md AC3). Carries the seeded counts
  so the panel chart shows Lane B's actual throughput.
- ``world_history.narrative_log_writeback`` — per-write span on the
  ``narrative_log.append`` driven by arc content. Pairs with the
  per-turn ``sd.store.append_narrative`` site already on the panel.
- ``world_history.lore_writeback`` — per-write span on the
  ``LoreStore.add`` driven by arc content. ``pending_embedding=True``
  attribute confirms the entry will be picked up by the existing
  ``lore_embedding.worker`` span.

Without ``SPAN_ROUTES`` registrations the watcher emits only the
always-on ``agent_span_close`` and the GM panel's typed Subsystems
tab silently drops the writeback events — exactly Felix's failure
mode (silent absence). Pin the routing decision so a future rename
or reshape trips a hard test failure rather than a dashboard gap.
"""

from __future__ import annotations

from sidequest.telemetry.spans import (
    SPAN_ROUTES,
    SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED,
    SPAN_WORLD_HISTORY_LORE_WRITEBACK,
    SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK,
)

# ---------------------------------------------------------------------------
# Constant value pins — the canonical span name is what the GM panel's
# filter rules key off. Renaming the constant must also rename the
# string literal, never silently.
# ---------------------------------------------------------------------------


def test_arc_embedding_seed_span_constant_value() -> None:
    assert (
        SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED
        == "world_history.arc_embedding_seed"
    )


def test_narrative_log_writeback_span_constant_value() -> None:
    assert (
        SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK
        == "world_history.narrative_log_writeback"
    )


def test_lore_writeback_span_constant_value() -> None:
    assert SPAN_WORLD_HISTORY_LORE_WRITEBACK == "world_history.lore_writeback"


# ---------------------------------------------------------------------------
# Routing — every span must be in SPAN_ROUTES with a state_transition
# event_type so the panel's typed Subsystems tab can chart it. Missing
# from the registry would route the span only via FLAT_ONLY_SPANS —
# i.e. into the agent_span_close stream — and the typed tab would
# silently lose the writeback events.
# ---------------------------------------------------------------------------


def test_arc_embedding_seed_is_routed_as_state_transition() -> None:
    assert SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED in SPAN_ROUTES, (
        "world_history.arc_embedding_seed must be registered in "
        "SPAN_ROUTES; FLAT_ONLY_SPANS would route it only to "
        "agent_span_close and the GM panel's Subsystems tab would "
        "not see arc-embedding seeds."
    )
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED]
    assert route.event_type == "state_transition"
    assert route.component, (
        "arc_embedding_seed route must declare a component name"
    )


def test_narrative_log_writeback_is_routed_as_state_transition() -> None:
    assert SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK in SPAN_ROUTES
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK]
    assert route.event_type == "state_transition"
    assert route.component


def test_lore_writeback_is_routed_as_state_transition() -> None:
    assert SPAN_WORLD_HISTORY_LORE_WRITEBACK in SPAN_ROUTES
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_LORE_WRITEBACK]
    assert route.event_type == "state_transition"
    assert route.component


# ---------------------------------------------------------------------------
# Extract callables — the watcher uses ``route.extract(span)`` to build
# the typed event payload. Missing fields here produce silent zero-value
# columns on the GM panel (the same failure mode 45-23 is closing).
# ---------------------------------------------------------------------------


def test_arc_embedding_seed_extract_pulls_seed_counts() -> None:
    """Per context-story-45-23.md OTEL §1: the seed span carries counts
    so the GM panel can chart Lane B throughput. All four counts must
    survive the extract() projection.
    """

    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_ARC_EMBEDDING_SEED]

    class _FakeSpan:
        name = "world_history.arc_embedding_seed"
        attributes = {
            "chapter_id": "early",
            "narrative_entries_appended": 3,
            "lore_fragments_minted": 2,
            "lore_fragments_skipped_duplicate": 1,
            "content_bytes_seeded": 1024,
            "interaction": 5,
        }

    fields = route.extract(_FakeSpan())
    for required in (
        "chapter_id",
        "narrative_entries_appended",
        "lore_fragments_minted",
        "lore_fragments_skipped_duplicate",
        "content_bytes_seeded",
        "interaction",
    ):
        assert required in fields, (
            f"arc_embedding_seed route extract() missing {required!r}; "
            f"got {sorted(fields)}"
        )


def test_narrative_log_writeback_extract_pulls_per_chapter_counts() -> None:
    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_NARRATIVE_LOG_WRITEBACK]

    class _FakeSpan:
        name = "world_history.narrative_log_writeback"
        attributes = {
            "chapter_id": "early",
            "entries_count": 2,
            "interaction": 5,
            "entry_type": "arc_promotion",
        }

    fields = route.extract(_FakeSpan())
    for required in (
        "chapter_id",
        "entries_count",
        "interaction",
        "entry_type",
    ):
        assert required in fields, (
            f"narrative_log_writeback extract() missing {required!r}; "
            f"got {sorted(fields)}"
        )


def test_lore_writeback_extract_pulls_per_fragment_attributes() -> None:
    """Per context-story-45-23.md OTEL §3: the lore_writeback span
    carries per-fragment attributes including ``pending_embedding=
    True`` — the load-bearing assertion that the seeded fragment will
    flow into the existing embed worker.
    """

    route = SPAN_ROUTES[SPAN_WORLD_HISTORY_LORE_WRITEBACK]

    class _FakeSpan:
        name = "world_history.lore_writeback"
        attributes = {
            "chapter_id": "early",
            "fragment_id": "lore_arc_early_0",
            "category": "history",
            "content_bytes": 64,
            "pending_embedding": True,
        }

    fields = route.extract(_FakeSpan())
    for required in (
        "chapter_id",
        "fragment_id",
        "category",
        "content_bytes",
        "pending_embedding",
    ):
        assert required in fields, (
            f"lore_writeback extract() missing {required!r}; "
            f"got {sorted(fields)}"
        )
