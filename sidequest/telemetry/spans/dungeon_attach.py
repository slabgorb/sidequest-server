"""Dungeon attach-decision span (Beneath Sünden Plan 7 §8).

The session seam ``attach_dungeon_to_session`` was a SILENT gate: a
non-beneath_sunden world returned ``None`` with no trace, and a
successful bootstrap emitted only the nested ``dungeon.materialize``
spans — never an attach-level decision event. The 2026-05-17 playtest
misdiagnosis (a fully-materialized live dungeon read by the architect
as "the gate returned None / dungeon never bootstrapped") proved the
silent path is a CLAUDE.md No-Silent-Fallbacks violation that defeats
the GM-panel lie detector. This span makes the attach decision
observable: every call emits exactly one routed event carrying the
genre/world the seam actually saw and the outcome it chose, so a
legitimate skip is forever distinguishable from a misfire.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_DUNGEON_ATTACH = "dungeon.attach"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_DUNGEON_ATTACH] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "dungeon_attach",
        "op": _attr("outcome")(s),
        "genre_slug": _attr("genre_slug")(s),
        "world_slug": _attr("world_slug")(s),
        "regions": _attr("regions")(s),
        "reason": _attr("reason")(s),
    },
)


@contextmanager
def dungeon_attach_span(
    *,
    genre_slug: str,
    world_slug: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the attach-decision span over the WHOLE seam body.

    The caller MUST set ``outcome`` before the context closes (and
    ``regions`` on a bootstrap) — the extractor reads span attributes
    at close as the single source of truth for the typed event:

    - ``skipped_other_world`` — gate returned None (not this dungeon's
      world); ``reason`` names the world that was seen instead.
    - ``bootstrapped`` — seed materialized this call; ``regions`` set.
    - ``already_seeded`` — map already had nodes; worker re-registered
      only (the legitimate sequential-reopen path).
    """
    with Span.open(
        SPAN_DUNGEON_ATTACH,
        {"genre_slug": genre_slug, "world_slug": world_slug, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_DUNGEON_ATTACH",
    "dungeon_attach_span",
]
