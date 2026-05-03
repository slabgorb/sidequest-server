"""Pure geometry helpers for course overlay rendering.

Separate from course.py so cost/selection logic and rendering math
each test in isolation. No SideQuest model imports — operates on
plain floats.
"""

from __future__ import annotations

import math


def chord_angular_distance_deg(phase_a_deg: float, phase_b_deg: float) -> float:
    """Short-arc angular distance between two phase angles, in degrees.

    Always returns the smaller of the two arcs (0 ≤ result ≤ 180).
    """
    diff = abs((phase_a_deg - phase_b_deg) % 360.0)
    return min(diff, 360.0 - diff)


def prograde_sign(party_phase_deg: float, dest_phase_deg: float) -> int:
    """+1 if destination is ahead of party in prograde (counter-clockwise);
    -1 if behind. Used to bulge the Bezier control points in the prograde
    direction so the arc reads as orbital-flavored.
    """
    delta = (dest_phase_deg - party_phase_deg) % 360.0
    return 1 if delta <= 180.0 else -1


def bezier_control_offset(chord_length: float, prograde: int) -> float:
    """Perpendicular offset for cubic Bezier control points.

    0.3 × chord_length in prograde direction. ``chord_length`` is in the
    same units as the SVG (radii from chart center, typically pixels or
    AU-derived units depending on caller).
    """
    return 0.3 * chord_length * prograde
