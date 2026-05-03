"""Course computation — selection, cost, validation.

Pure module: no I/O, no global state. Deterministic given its inputs.
The renderer (course_render.py) and handler (handlers/course_intent.py)
import from here; nothing imports those upward.
"""

from __future__ import annotations

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
