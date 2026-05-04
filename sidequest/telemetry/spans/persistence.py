"""Persistence spans — save/load/delete of SQLite session files."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_PERSISTENCE_SAVE = "persistence_save"
SPAN_PERSISTENCE_LOAD = "persistence_load"
SPAN_PERSISTENCE_DELETE = "persistence_delete"

FLAT_ONLY_SPANS.update(
    {
        SPAN_PERSISTENCE_SAVE,
        SPAN_PERSISTENCE_LOAD,
        SPAN_PERSISTENCE_DELETE,
    }
)


# ---------------------------------------------------------------------------
# Snapshot canonicalize — sidequest/game/migrations.py
# Emitted by ``SqliteStore.load`` when ``migrate_legacy_snapshot`` rewrote
# any field. Per-field migration markers are span attributes (e.g.
# ``s1_world_confrontations_merged: int``). Lie-detector hook for the GM
# panel — Sebastien sees which legacy split-brain shapes are still in the
# wild.
#
# Honesty rule: the extractor only forwards keys that an actual migration
# sub-function emits. S4 (Python class rename) and S5 (``Field(exclude=True)``)
# are NOT per-save migrations — they leave no on-disk trace and have no
# sub-function. Reporting hardcoded ``s4_encounter_tag_renamed: false`` /
# ``s5_pending_queues_dropped: 0`` would lie to the GM panel. When/if a
# future migration sub-function legitimately emits an attribute, extend
# this dict alongside the sub-function — never before.
# ---------------------------------------------------------------------------
SPAN_SNAPSHOT_CANONICALIZE = "snapshot.canonicalize"


def _extract_snapshot_canonicalize(span: Any) -> dict[str, Any]:
    """Forward only the per-field migration attributes the span carries.

    No defaulted keys: if a sub-function did not register an attribute for
    this load, the GM panel must not see a value for it. Otherwise the
    extractor invents zero/false markers for migrations that never ran.
    """
    payload: dict[str, Any] = {"field": "snapshot", "op": "canonicalize"}
    attrs = span.attributes or {}
    for key in (
        "s1_world_confrontations_merged",
        "s1_world_confrontations_dropped_no_target",
    ):
        if key in attrs:
            payload[key] = attrs[key]
    return payload


SPAN_ROUTES[SPAN_SNAPSHOT_CANONICALIZE] = SpanRoute(
    event_type="state_transition",
    component="persistence",
    extract=_extract_snapshot_canonicalize,
)


# ---------------------------------------------------------------------------
# Session lifecycle — sidequest/game/persistence.py
# Fires every time SqliteStore.init_session() runs — including on a fresh
# slot — so the GM panel gets the negative confirmation that reinit ran
# cleanly (CLAUDE.md observability principle: a silent half-clear
# regression must not be invisible).
# ---------------------------------------------------------------------------
SPAN_SESSION_SLOT_REINITIALIZED = "session.slot_reinitialized"
SPAN_ROUTES[SPAN_SESSION_SLOT_REINITIALIZED] = SpanRoute(
    event_type="state_transition",
    component="session",
    extract=lambda span: {
        "field": "session_meta",
        "op": "slot_reinitialized",
        "genre_slug": (span.attributes or {}).get("genre_slug", ""),
        "world_slug": (span.attributes or {}).get("world_slug", ""),
        "cleared_tables": (span.attributes or {}).get("cleared_tables", []),
        "prior_narrative_count": (span.attributes or {}).get("prior_narrative_count", 0),
        "prior_event_count": (span.attributes or {}).get("prior_event_count", 0),
        "mode": (span.attributes or {}).get("mode", "clear"),
    },
)


@contextmanager
def persistence_save_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_PERSISTENCE_SAVE,
        {"genre": genre, "world": world, "player": player, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def persistence_load_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_PERSISTENCE_LOAD,
        {"genre": genre, "world": world, "player": player, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
