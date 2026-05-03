"""Apply plot_course / cancel_course sidecar intents to the snapshot.

Pure-ish: mutates the passed snapshot but has no other side effects
(OTEL emission lives in callers, not here, so unit tests stay fast).
"""

from __future__ import annotations

from dataclasses import dataclass

from sidequest.game.session import GameSnapshot
from sidequest.orbital.course import CourseRow, PlottedCourse
from sidequest.protocol.course_intent import (
    CancelCourseSidecar,
    CourseSidecar,
    PlotCourseSidecar,
)


@dataclass(frozen=True)
class CourseHandlerResult:
    """Outcome of applying one course sidecar.

    Callers (narration_apply) emit OTEL based on these fields and
    surface rejected reasons into the next turn's reactions zone.
    """

    accepted: bool
    reason: str = ""
    was_already_clear: bool = False
    """True for cancel_course when there was no plot to clear (no-op)."""


def handle_course_sidecar(
    *,
    sidecar: CourseSidecar,
    snapshot: GameSnapshot,
    available_courses: dict[str, CourseRow],
) -> CourseHandlerResult:
    """Apply ``sidecar`` to ``snapshot`` in-place.

    For ``PlotCourseSidecar``: requires ``course_id`` to be a key in
    ``available_courses`` (the compute_courses output for THIS turn).
    Sets ``snapshot.plotted_course`` on accept; leaves it untouched
    on reject.

    For ``CancelCourseSidecar``: clears ``snapshot.plotted_course``.
    No-op when already clear, but still ``accepted=True``.
    """
    if isinstance(sidecar, PlotCourseSidecar):
        return _handle_plot(sidecar, snapshot, available_courses)
    if isinstance(sidecar, CancelCourseSidecar):
        return _handle_cancel(snapshot)
    # Type system says exhaustive; this is a safety net.
    return CourseHandlerResult(accepted=False, reason="unknown_intent")


def _handle_plot(
    sidecar: PlotCourseSidecar,
    snapshot: GameSnapshot,
    available_courses: dict[str, CourseRow],
) -> CourseHandlerResult:
    row = available_courses.get(sidecar.course_id)
    if row is None:
        return CourseHandlerResult(
            accepted=False,
            reason=f"not_in_scope:course_id={sidecar.course_id!r}",
        )
    snapshot.plotted_course = PlottedCourse(
        to_body_id=row.to_body_id,
        label=row.label_hint,
        eta_hours=row.eta_hours,
        delta_v=row.delta_v,
        plotted_at_t_hours=snapshot.clock_t_hours,
        source=row.source,
    )
    return CourseHandlerResult(accepted=True)


def _handle_cancel(snapshot: GameSnapshot) -> CourseHandlerResult:
    if snapshot.plotted_course is None:
        return CourseHandlerResult(accepted=True, was_already_clear=True)
    snapshot.plotted_course = None
    return CourseHandlerResult(accepted=True)
