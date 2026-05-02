"""chart.* OTEL spans — orbital chart rendering.

Per spec §7.3 — Keith's GM panel sees scope_center, t_hours, party_at,
body_count, output_size_bytes for every chart render. Validates that
re-renders happen on the expected schedule (clock advance, drill
in/out) and that the renderer isn't quietly producing empty SVGs.
"""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_CHART_RENDER = "chart.render"

FLAT_ONLY_SPANS.update({SPAN_CHART_RENDER})


def emit_chart_render(
    *,
    scope_center: str,
    t_hours: float,
    party_at: str | None,
    body_count: int,
    output_size_bytes: int,
) -> None:
    """Emit a `chart.render` span. Fire-and-forget (FLAT_ONLY_SPANS)."""
    with Span.open(
        SPAN_CHART_RENDER,
        attrs={
            "scope_center": scope_center,
            "t_hours": float(t_hours),
            "party_at": party_at if party_at is not None else "",
            "body_count": int(body_count),
            "output_size_bytes": int(output_size_bytes),
        },
    ):
        pass
