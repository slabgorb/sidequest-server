"""OTEL spans for the course-plotting subsystem.

# Bundle 6 will replace these no-op stubs with real OTEL emitters.
# Each function here is a stub for Bundle 6 (Task 10: course span helpers).
# Pattern mirrors sidequest/telemetry/spans/chart.py and interior.py.
# Per CLAUDE.md OTEL principle: every backend subsystem MUST emit
# spans so the GM dashboard can verify the lie-detector pattern
# (prose vs map disagreement is invisible without telemetry).

Stubs are intentional — Bundle 5 wires the handler and call sites;
Bundle 6 replaces these with real span emission using Span.open() and
SPAN_ROUTES registration so the GM panel can see course decisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sidequest.orbital.course import PlottedCourse


# stub for Bundle 6
def emit_course_compute(
    *,
    course_count: int,
    in_scope: int,
    recent: int,
    quest: int,
    dropped_by_cap: int,
) -> None:
    """Emit a course.compute span. stub for Bundle 6."""
    pass


# stub for Bundle 6
def emit_course_plot_accepted(
    *,
    from_body: str | None,
    course: "PlottedCourse | None",
) -> None:
    """Emit a course.plot span on accepted plot_course. stub for Bundle 6."""
    pass


# stub for Bundle 6
def emit_course_plot_rejected(
    *,
    course_id: str,
    reason: str,
    available_ids: list[str],
) -> None:
    """Emit a course.plot.rejected span. stub for Bundle 6."""
    pass


# stub for Bundle 6
def emit_course_cancel(
    *,
    was_already_clear: bool,
) -> None:
    """Emit a course.cancel span. stub for Bundle 6."""
    pass


# stub for Bundle 6
def emit_course_render_overlay(
    *,
    to_body: str,
    bezier_control_offset_au: float,
) -> None:
    """Emit a course.render_overlay span. stub for Bundle 6."""
    pass
