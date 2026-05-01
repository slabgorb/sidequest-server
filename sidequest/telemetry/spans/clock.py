"""clock.* OTEL span — every beat-driven story-time advance.

Per spec §3.3 and §7.3: the GM panel relies on this span to verify that
every story-time advance happened via a real beat (no silent skips, no
narrator improvisation of duration).
"""
from __future__ import annotations

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_CLOCK_ADVANCE = "clock.advance"

FLAT_ONLY_SPANS.update({SPAN_CLOCK_ADVANCE})


def emit_clock_advance(
    *,
    beat_kind: str,
    duration_hours: float,
    t_before_h: float,
    t_after_h: float,
    trigger: str,
) -> None:
    """Emit a `clock.advance` span. Fire-and-forget (FLAT_ONLY_SPANS)."""
    with Span.open(SPAN_CLOCK_ADVANCE, attrs={
        "beat_kind": beat_kind,
        "duration_hours": float(duration_hours),
        "t_before_h": float(t_before_h),
        "t_after_h": float(t_after_h),
        "trigger": trigger,
    }):
        pass
