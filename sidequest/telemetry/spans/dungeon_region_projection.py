"""Dungeon per-turn region-projection span (Beneath Sünden BETTER fix).

The materialized dungeon was orphaned from the narrator: the prompt
never saw the current region's prose/exits, so the narrator improvised
geography (2026-05-17 playtest). This span makes the per-turn projection
seam observable — every narration turn in a beneath_sunden session emits
exactly one routed ``dungeon.region_projection`` event carrying the
region id, theme, depth, and the concrete exit count handed to the
narrator as the constrained move vocabulary. The GM panel (the lie
detector) can now tell "narrator was fed the real region" from
"narrator improvised" without reading prose.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_DUNGEON_REGION_PROJECTION = "dungeon.region_projection"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_DUNGEON_REGION_PROJECTION] = SpanRoute(
    event_type="state_transition",
    component="dungeon",
    extract=lambda s: {
        "field": "region_projection",
        "op": _attr("outcome")(s),
        "region_id": _attr("region_id")(s),
        "theme_id": _attr("theme_id")(s),
        "depth_score": _attr("depth_score")(s),
        "exit_count": _attr("exit_count")(s),
        "reason": _attr("reason")(s),
    },
)


@contextmanager
def dungeon_region_projection_span(
    *,
    current_region: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the region-projection span over the per-turn projection.

    The caller MUST set ``outcome`` before the context closes:

    - ``projected`` — a real region was projected into the narrator
      prompt + move vocabulary; ``region_id`` / ``theme_id`` /
      ``depth_score`` / ``exit_count`` set.
    - ``no_dungeon`` — session has no materialized dungeon (non-
      beneath_sunden world, or store has no dungeon schema); ``reason``
      names why. A clean, observable no-op — never a silent skip.
    """
    with Span.open(
        SPAN_DUNGEON_REGION_PROJECTION,
        {"current_region": current_region, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_DUNGEON_REGION_PROJECTION",
    "dungeon_region_projection_span",
]
