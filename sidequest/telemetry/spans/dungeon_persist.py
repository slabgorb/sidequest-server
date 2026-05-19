"""Dungeon persistence spans (Beneath Sünden Plan 5 §6).

Only spans with a REAL store-method caller live here: commit, ledger
add, ledger resolve. The materializer / frontier-expand spans are
Plan 7's — emitting them here with no caller would be the exact
Illusionism the GM panel exists to catch (spec §6).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_DUNGEON_PERSIST_COMMIT = "dungeon.persist.commit"
SPAN_DUNGEON_PERSIST_MASK_WRITE = "dungeon.persist.mask_write"
SPAN_DUNGEON_PERSIST_MASK_LOAD = "dungeon.persist.mask_load"
SPAN_LEDGER_ADD = "ledger.add"
SPAN_LEDGER_RESOLVE = "ledger.resolve"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_DUNGEON_PERSIST_COMMIT] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map",
        "op": "commit_expansion",
        "expansion_id": _attr("expansion_id")(s),
        "regions": _attr("regions")(s),
        "edges": _attr("edges")(s),
        "generator_version": _attr("generator_version")(s),
    },
)
SPAN_ROUTES[SPAN_DUNGEON_PERSIST_MASK_WRITE] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map.mask",
        "op": "write_masks",
        "mask_rows": _attr("mask_rows")(s),
    },
)
SPAN_ROUTES[SPAN_DUNGEON_PERSIST_MASK_LOAD] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_map.mask",
        "op": "load_masks",
        "mask_rows": _attr("mask_rows")(s),
    },
)
SPAN_ROUTES[SPAN_LEDGER_ADD] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "complication_ledger",
        "op": "open_thread",
        "thread_id": _attr("thread_id")(s),
        "kind": _attr("kind")(s),
        "origin_region_id": _attr("origin_region_id")(s),
    },
)
SPAN_ROUTES[SPAN_LEDGER_RESOLVE] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "complication_ledger",
        "op": "resolve_thread",
        "thread_id": _attr("thread_id")(s),
    },
)


@contextmanager
def dungeon_persist_commit_span(
    *,
    expansion_id: int,
    regions: int,
    edges: int,
    generator_version: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_DUNGEON_PERSIST_COMMIT,
        {
            "expansion_id": expansion_id,
            "regions": regions,
            "edges": edges,
            "generator_version": generator_version,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def mask_write_span(
    *,
    mask_rows: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_DUNGEON_PERSIST_MASK_WRITE,
        {"mask_rows": mask_rows, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def mask_load_span(
    *,
    mask_rows: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_DUNGEON_PERSIST_MASK_LOAD,
        {"mask_rows": mask_rows, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def ledger_add_span(
    *,
    thread_id: str,
    kind: str,
    origin_region_id: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_LEDGER_ADD,
        {
            "thread_id": thread_id,
            "kind": kind,
            "origin_region_id": origin_region_id,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def ledger_resolve_span(
    *,
    thread_id: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_LEDGER_RESOLVE,
        {"thread_id": thread_id, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_DUNGEON_PERSIST_COMMIT",
    "SPAN_DUNGEON_PERSIST_MASK_LOAD",
    "SPAN_DUNGEON_PERSIST_MASK_WRITE",
    "SPAN_LEDGER_ADD",
    "SPAN_LEDGER_RESOLVE",
    "dungeon_persist_commit_span",
    "ledger_add_span",
    "ledger_resolve_span",
    "mask_load_span",
    "mask_write_span",
]
