"""Dungeon set-piece attach spans (Beneath Sünden Plan 6 §6).

Four spans live here, each with a real caller in setpiece_attach.py:
- ``trope.start`` — start_trope_components (Task 2). Has a failure path:
  the span emits failed=True BEFORE re-raising on an unknown trope_id so
  the GM panel sees the content authoring bug rather than silence.
- ``quest.seed`` — seed_quest_components (Task 3). Informational/success
  span ONLY — reduced Task 3 has NO failure path. The set-piece↔cookbook
  creature/loot manifest-join (the only thing that could surface a
  content bug at quest-seed) is REASSIGNED TO PLAN 7 by Architect
  decision (Plan 4 shipped no ref convention; see the plan's
  Post-Implementation Corrections). A fabricated ``failed`` attribute
  would be testing theater (the inverse of stubbing), so quest.seed opens
  and closes clean per seeded component — no ``failed`` attribute exists.
- ``setpiece.attach`` — attach_set_piece (Task 4). One span per set-piece
  attach, emitted after all thread writes complete. Carries AttachReport
  fields as attributes (the same way DepthReport feeds
  ``dungeon.materialize.attach``). The single coalescence span Plan 7 uses
  as its attach-stage lie detector.
- ``setpiece.resolve`` — resolve_complications_for_resolved_tropes
  (Task 5). One aggregate span per resolution-subscription call (every
  turn the 45-20 handshake fires). Carries ``tropes_processed`` (how many
  resolved trope_ids the subscription was handed) and ``threads_resolved``
  (how many open ledger threads it actually flipped). REQUIRED by the
  CLAUDE.md OTEL Observability Principle: without it the GM panel cannot
  distinguish "subscription fired, correctly found 0 matching threads"
  from "subscription never ran". This is the Plan-6-owned aggregate
  subsystem-decision span; Plan 5 still owns the per-thread ``ledger.*``
  spans underneath (do NOT re-emit ``ledger.resolve`` here — Seam-1
  supersession: Plan 5 owns it).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_TROPE_START = "trope.start"
SPAN_QUEST_SEED = "quest.seed"
SPAN_SETPIECE_ATTACH = "setpiece.attach"
SPAN_SETPIECE_RESOLVE = "setpiece.resolve"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_TROPE_START] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "active_tropes",
        "op": "trope_start",
        "trope_id": _attr("trope_id")(s),
        "setpiece_id": _attr("setpiece_id")(s),
        "origin_region_id": _attr("origin_region_id")(s),
        "failed": _attr("failed")(s),
    },
)


# quest.seed mirrors trope.start's routing (component="dungeon",
# state_transition) — a seeded quest is a future
# ComplicationThread(kind="quest") that Task 4 writes to the ledger. No
# ``failed`` key in the extract: reduced Task 3 has no failure path (the
# manifest-join that could surface a content bug is Plan 7's — see module
# docstring), so emitting a failed attribute would be testing theater.
SPAN_ROUTES[SPAN_QUEST_SEED] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "complication_ledger",
        "op": "quest_seed",
        "quest_id": _attr("quest_id")(s),
        "setpiece_id": _attr("setpiece_id")(s),
        "origin_region_id": _attr("origin_region_id")(s),
    },
)


@contextmanager
def trope_start_span(
    *,
    trope_id: str,
    setpiece_id: str,
    origin_region_id: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open a trope.start span (opens with failed=False).

    The failure path is signalled post-yield via
    ``span.set_attribute("failed", True)`` on the yielded span before the
    caller re-raises — there is no open-time failure-declaration API. The
    span is opened with ``failed=False``; an unknown trope_id flips it on
    the live span so the GM panel sees the content bug, not silence.
    """
    with Span.open(
        SPAN_TROPE_START,
        {
            "trope_id": trope_id,
            "setpiece_id": setpiece_id,
            "origin_region_id": origin_region_id,
            "failed": False,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def quest_seed_span(
    *,
    quest_id: str,
    setpiece_id: str,
    origin_region_id: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open a quest.seed span for one seeded quest component.

    Informational/success span ONLY. Unlike trope_start_span there is NO
    ``failed`` attribute and NO failure path: reduced Task 3 cannot raise a
    content bug (no quest registry to resolve against; the creature/loot
    manifest-join is Plan 7's by Architect decision — see module docstring
    and the plan's Post-Implementation Corrections). The span opens and
    closes clean; it exists so the GM panel sees the per-quest seed trail
    (one span per seeded component) the same way it sees trope.start.
    """
    with Span.open(
        SPAN_QUEST_SEED,
        {
            "quest_id": quest_id,
            "setpiece_id": setpiece_id,
            "origin_region_id": origin_region_id,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


# setpiece.attach — one span per set-piece attach coalescence (Task 4).
# Carries AttachReport fields as attributes (mirrors DepthReport feeding
# dungeon.materialize.attach). Plan 7's attach stage is the consumer.
SPAN_ROUTES[SPAN_SETPIECE_ATTACH] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "complication_ledger",
        "op": "setpiece_attach",
        "setpiece_id": _attr("setpiece_id")(s),
        "region_id": _attr("region_id")(s),
        "tropes_started": _attr("tropes_started")(s),
        "quests_seeded": _attr("quests_seeded")(s),
        "threads_written": _attr("threads_written")(s),
    },
)


@contextmanager
def setpiece_attach_span(
    *,
    setpiece_id: str,
    region_id: str,
    tropes_started: int,
    quests_seeded: int,
    threads_written: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open a setpiece.attach span carrying the AttachReport contract fields.

    One span per set-piece coalescence (emitted by attach_set_piece after all
    ComplicationThread writes complete). Carries the locked AttachReport.as_dict()
    key set as attributes so Plan 7's attach-stage OTEL and the GM panel can
    verify the attach completed and how many threads were written.

    The span attributes mirror AttachReport.as_dict() exactly (Decision K):
    setpiece_id, region_id, tropes_started, quests_seeded, threads_written.
    """
    with Span.open(
        SPAN_SETPIECE_ATTACH,
        {
            "setpiece_id": setpiece_id,
            "region_id": region_id,
            "tropes_started": tropes_started,
            "quests_seeded": quests_seeded,
            "threads_written": threads_written,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


# setpiece.resolve — one aggregate span per resolution-subscription call
# (Task 5). Emitted by resolve_complications_for_resolved_tropes every time
# the 45-20 handshake fires. tropes_processed = #resolved trope_ids handed
# in; threads_resolved = #open ledger threads actually flipped. This is the
# Plan-6-owned subsystem-decision span the OTEL Observability Principle
# requires — Plan 5's per-thread ledger.resolve stays underneath unchanged.
SPAN_ROUTES[SPAN_SETPIECE_RESOLVE] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "complication_ledger",
        "op": "setpiece_resolve",
        "tropes_processed": _attr("tropes_processed")(s),
        "threads_resolved": _attr("threads_resolved")(s),
    },
)


@contextmanager
def setpiece_resolve_span(
    *,
    tropes_processed: int,
    threads_resolved: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the aggregate setpiece.resolve span for one resolution call.

    One span per ``resolve_complications_for_resolved_tropes`` invocation
    (every turn the 45-20 handshake fires). ``tropes_processed`` is the
    count of resolved trope_ids the subscription was handed (= the
    handshake diff size); ``threads_resolved`` is the count of open ledger
    threads it actually flipped. Both are late-bound by the caller after
    the resolution loop completes so the GM panel sees the subscription
    fired AND what it did — distinguishing "fired, 0 matches" from "never
    ran" (CLAUDE.md OTEL Observability Principle).

    Plan 5 still owns the per-thread ``ledger.resolve`` span inside
    ``store.resolve_thread``; this aggregate does NOT re-emit it
    (Seam-1 supersession).
    """
    with Span.open(
        SPAN_SETPIECE_RESOLVE,
        {
            "tropes_processed": tropes_processed,
            "threads_resolved": threads_resolved,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_QUEST_SEED",
    "SPAN_SETPIECE_ATTACH",
    "SPAN_SETPIECE_RESOLVE",
    "SPAN_TROPE_START",
    "quest_seed_span",
    "setpiece_attach_span",
    "setpiece_resolve_span",
    "trope_start_span",
]
