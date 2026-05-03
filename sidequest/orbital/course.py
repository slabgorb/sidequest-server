"""Course computation — selection, cost, validation.

Pure module: no I/O, no global state. Deterministic given its inputs.
The renderer (course_render.py) and handler (handlers/course_intent.py)
import from here; nothing imports those upward.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from sidequest.orbital.course_geometry import chord_angular_distance_deg
from sidequest.orbital.models import BodyDef, OrbitsConfig

# Calibration constants — tuned so Far Landing → Tethys Watch ≈ 12h,
# Far Landing → The Gate ≈ 90h. See cost-model section of the design.
TRAVEL_HOURS_PER_AU = 30.0
DELTA_V_BASE = 0.7           # km/s per AU of total chord distance
DELTA_V_RADIAL_FACTOR = 0.4  # extra Δv per AU of radial (semi-major-axis) diff


def compute_eta_and_dv(
    party_at: BodyDef,
    dest: BodyDef,
    orbits: OrbitsConfig,
) -> tuple[float, float]:
    """Hohmann-flavored cost. NOT real orbital mechanics.

    Returns ``(eta_hours, delta_v_km_per_s)``. Both 0.0 when the two
    bodies are identical references (same physical body, same phase).

    Inputs:
    - ``party_at``, ``dest``: ``BodyDef`` instances from the world's
      orbits.yaml. Either may be a moon (parent != system root); we
      treat ``semi_major_au`` as a flat distance proxy regardless.
    - ``orbits``: needed for ``travel.travel_speed_factor``.
    """
    if party_at is dest:
        return 0.0, 0.0
    a1 = party_at.semi_major_au or 0.0
    a2 = dest.semi_major_au or 0.0
    radial_au = abs(a1 - a2)
    phase_a = party_at.epoch_phase_deg or 0.0
    phase_b = dest.epoch_phase_deg or 0.0
    angular_au = 0.05 * (chord_angular_distance_deg(phase_a, phase_b) / 360.0)
    chord_au = radial_au + angular_au
    eta_hours = (chord_au * TRAVEL_HOURS_PER_AU) / orbits.travel.travel_speed_factor
    delta_v = chord_au * DELTA_V_BASE + radial_au * DELTA_V_RADIAL_FACTOR
    return eta_hours, delta_v


class CourseSource(StrEnum):
    """Why a course was offered. Drives the 12-cap priority ordering."""

    IN_SCOPE = "in_scope"
    RECENT_MENTION = "recent_mention"
    QUEST_OBJECTIVE = "quest_objective"

    @property
    def priority(self) -> int:
        """Higher = keep when capping. Quest > recent > in-scope."""
        return _SOURCE_PRIORITY[self]


_SOURCE_PRIORITY: dict[CourseSource, int] = {
    CourseSource.IN_SCOPE: 1,
    CourseSource.RECENT_MENTION: 2,
    CourseSource.QUEST_OBJECTIVE: 3,
}


class CourseRow(BaseModel):
    """One precomputed course exposed to narrator + GM panel.

    Labelled "row" because the prompt block renders these as one bullet
    each. Distinct from PlottedCourse, which is the snapshot field
    representing the *committed* (well, plotted) course.
    """

    model_config = ConfigDict(extra="forbid")

    to_body_id: str
    eta_hours: float
    delta_v: float
    source: CourseSource
    label_hint: str | None = None  # quest objective name when source=QUEST_OBJECTIVE


class PlottedCourse(BaseModel):
    """The snapshot's persistent course state — drawn on the chart.

    Cleared by replace, cancel, or arrival (party_body_id == to_body_id).
    Survives save/load and WebSocket disconnect by virtue of being a
    snapshot field.
    """

    model_config = ConfigDict(extra="forbid")

    to_body_id: str
    label: str | None = None
    eta_hours: float
    delta_v: float
    plotted_at_t_hours: float
    source: CourseSource
