"""Dungeon materializer spans (Beneath Sünden Plan 7 §OTEL).

Every span constant owned by Plan 7's materializer pipeline lives here.
The five stage spans nest under the parent ``dungeon.materialize`` span
for the duration of one ``materialize()`` call. ``frontier.expand`` is
also Plan-7-owned (the async look-ahead worker fires it when it picks the
next edge to expand); the helper is provided now so the catalog is complete
and routed even though the worker is a later task.

No spans were emitted here before this module — Plan 5/6 deferred them
deliberately (emitting spans with no caller would be the exact Illusionism
the GM panel exists to catch). Plan 7 is the first caller.
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

SPAN_DUNGEON_MATERIALIZE = "dungeon.materialize"
SPAN_DUNGEON_MATERIALIZE_DESIGN = "dungeon.materialize.design"
SPAN_DUNGEON_MATERIALIZE_FILL = "dungeon.materialize.fill"
SPAN_DUNGEON_MATERIALIZE_CURATE = "dungeon.materialize.curate"
SPAN_DUNGEON_MATERIALIZE_ATTACH = "dungeon.materialize.attach"
SPAN_DUNGEON_MATERIALIZE_COMMIT = "dungeon.materialize.commit"
SPAN_FRONTIER_EXPAND = "frontier.expand"

# ---------------------------------------------------------------------------
# Routing registrations
# ---------------------------------------------------------------------------


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize",
        "expansion_id": _attr("expansion_id")(s),
        "heading": _attr("heading")(s),
        "burst_magnitude": _attr("burst_magnitude")(s),
    },
)

SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_DESIGN] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    # report.as_dict() keys are the byte-pinned attribute contract (Plan 7 Task 2).
    # error/failing are the ExpansionGenerationError lie-detector markers: they
    # read None on the success path (graceful-get idiom — harmless) and surface
    # the generation failure on the GM panel on the failure path.
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize.design",
        "expansion_id": _attr("expansion_id")(s),
        "attempts": _attr("attempts")(s),
        "stitch_edges": _attr("stitch_edges")(s),
        "loops_into_explored": _attr("loops_into_explored")(s),
        "hidden_edges": _attr("hidden_edges")(s),
        "shortcut_edges": _attr("shortcut_edges")(s),
        "new_regions": _attr("new_regions")(s),
        "invariants_passed": _attr("invariants_passed")(s),
        "error": _attr("error")(s),
        "failing": _attr("failing")(s),
    },
)

SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_FILL] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    # `regions` is the per-region fill payload (Plan 7 Task 3): a JSON list
    # of {region_id, algorithm, width, height, braid_ratio} — the
    # ACTUALLY-applied braid_ratio per region (lie-detector: proves it was
    # not silently defaulted). `error` is the failure marker: it reads None
    # on the success path (graceful-get idiom — harmless) and surfaces a
    # missing-theme / roomcorridor-floor / degenerate-seed / unknown-algorithm
    # failure on the GM panel on the failure path.
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize.fill",
        "expansion_id": _attr("expansion_id")(s),
        "stage": "fill",
        "regions": _attr("regions")(s),
        "region_count": _attr("region_count")(s),
        "error": _attr("error")(s),
    },
)

SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_CURATE] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    # `curated` is the lie-detector verdict (Plan 7 Task 4): True only when
    # the bounded `claude -p` curation pass succeeded AND every corpus
    # creature was CR→Edge translated; False (with a specific `reason`) on
    # any failure path (assemble error / subprocess failure / unparseable
    # verdict). The Task-2 lesson: a set-but-not-routed marker is a defect
    # — `curated`/`reason` are routed here so the GM panel renders the
    # failure, never a raw-manifest-stamped-curated lie. The success
    # summary (region/creature counts, races, cr_bands) reads None on the
    # failure path via the graceful-get idiom (harmless).
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize.curate",
        "expansion_id": _attr("expansion_id")(s),
        "stage": "curate",
        "curated": _attr("curated")(s),
        "reason": _attr("reason")(s),
        "region_count": _attr("region_count")(s),
        "creature_count": _attr("creature_count")(s),
        "manifest_race": _attr("manifest_race")(s),
        "cr_band": _attr("cr_band")(s),
        "raw_seed_reproducible": _attr("raw_seed_reproducible")(s),
    },
)

SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_ATTACH] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize.attach",
        "expansion_id": _attr("expansion_id")(s),
        "stage": "attach",
    },
)

SPAN_ROUTES[SPAN_DUNGEON_MATERIALIZE_COMMIT] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "materialize.commit",
        "expansion_id": _attr("expansion_id")(s),
        "stage": "commit",
    },
)

SPAN_ROUTES[SPAN_FRONTIER_EXPAND] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_frontier",
        "op": "frontier_expand",
        "expansion_id": _attr("expansion_id")(s),
        "frontier_edge_id": _attr("frontier_edge_id")(s),
    },
)

# ---------------------------------------------------------------------------
# Context-manager helpers
# ---------------------------------------------------------------------------


@contextmanager
def dungeon_materialize_span(
    *,
    expansion_id: int,
    heading: str,
    burst_magnitude: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the parent ``dungeon.materialize`` span for one materialize() call."""
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE,
        {
            "expansion_id": expansion_id,
            "heading": heading,
            "burst_magnitude": burst_magnitude,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def dungeon_materialize_design_span(
    *,
    expansion_id: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``dungeon.materialize.design`` child span.

    No ``stage`` attribute is pre-baked here: the design stage itself writes
    exactly ``report.as_dict()`` onto the span after ``generate_expansion``
    returns (byte-pinned GM-panel contract, Plan 7 Task 2).  The only
    pre-baked attribute is ``expansion_id``; the stage's ``set_attribute``
    calls overwrite it with the same value from the report.
    """
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE_DESIGN,
        {"expansion_id": expansion_id, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def dungeon_materialize_fill_span(
    *,
    expansion_id: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``dungeon.materialize.fill`` child span."""
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE_FILL,
        {"expansion_id": expansion_id, "stage": "fill", **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def dungeon_materialize_curate_span(
    *,
    expansion_id: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``dungeon.materialize.curate`` child span."""
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE_CURATE,
        {"expansion_id": expansion_id, "stage": "curate", **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def dungeon_materialize_attach_span(
    *,
    expansion_id: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``dungeon.materialize.attach`` child span."""
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE_ATTACH,
        {"expansion_id": expansion_id, "stage": "attach", **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def dungeon_materialize_commit_span(
    *,
    expansion_id: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``dungeon.materialize.commit`` child span."""
    with Span.open(
        SPAN_DUNGEON_MATERIALIZE_COMMIT,
        {"expansion_id": expansion_id, "stage": "commit", **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def frontier_expand_span(
    *,
    expansion_id: int,
    frontier_edge_id: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the ``frontier.expand`` span for the async look-ahead worker."""
    with Span.open(
        SPAN_FRONTIER_EXPAND,
        {
            "expansion_id": expansion_id,
            "frontier_edge_id": frontier_edge_id,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_DUNGEON_MATERIALIZE",
    "SPAN_DUNGEON_MATERIALIZE_ATTACH",
    "SPAN_DUNGEON_MATERIALIZE_COMMIT",
    "SPAN_DUNGEON_MATERIALIZE_CURATE",
    "SPAN_DUNGEON_MATERIALIZE_DESIGN",
    "SPAN_DUNGEON_MATERIALIZE_FILL",
    "SPAN_FRONTIER_EXPAND",
    "dungeon_materialize_attach_span",
    "dungeon_materialize_commit_span",
    "dungeon_materialize_curate_span",
    "dungeon_materialize_design_span",
    "dungeon_materialize_fill_span",
    "dungeon_materialize_span",
    "frontier_expand_span",
]
