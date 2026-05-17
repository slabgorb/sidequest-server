"""Dungeon set-piece attach spans (Beneath Sünden Plan 6 §6).

Two spans live here, each with a real caller in setpiece_attach.py:
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

``setpiece.attach`` is still deferred — added when Task 4 ships its
caller; no stub here (CLAUDE.md: No Stubbing).
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


__all__ = [
    "SPAN_QUEST_SEED",
    "SPAN_TROPE_START",
    "quest_seed_span",
    "trope_start_span",
]
