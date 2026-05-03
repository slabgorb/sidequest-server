"""Eccentric Keplerian position math for the orbital chart.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §9.
Drop-in replacement for the prior circular approximation in `render._body_position_au_polar`.

Conventions:
  - The parent body is at the origin (focus of the orbit ellipse).
  - `epoch_phase_deg` is interpreted as the *mean anomaly* at t=0 (degrees).
    For circular orbits (e=0) this matches the body's position angle exactly,
    so behavior is unchanged for all data with eccentricity=0.
  - Argument of periapsis ω is assumed 0 — periapsis points along +x (3
    o'clock). Spec §12: rotation deferred until any world authors a non-zero ω.
  - Returned theta is the true anomaly ν (degrees, mod 360), measured CCW
    from +x. Composes with the existing `_polar_to_cartesian` SVG mapping.

Newton tolerance: 1e-6 rad — overkill for visual rendering but cheap (~3-5
iterations for e<0.5). Kept tight so unit tests can pin exact positions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sidequest.orbital.models import BodyDef

_NEWTON_TOL = 1e-6
_NEWTON_MAX_ITER = 8


@dataclass(frozen=True)
class EllipseSpec:
    """Geometry of an orbit ellipse for SVG rendering.

    Fields are in pixels relative to the focus (parent body) at origin.
    For ω=0, the ellipse major axis lies along ±x with periapsis at +x;
    the ellipse center is shifted left by c=a·e from the focus.
    """

    center_x_px: float
    center_y_px: float
    semi_major_px: float
    semi_minor_px: float


def kepler_position(body: BodyDef, t_hours: float) -> tuple[float, float]:
    """Body position (au, theta_deg) at story-time t, relative to parent.

    Honors `body.eccentricity`. theta is the true anomaly ν measured CCW
    from +x (3 o'clock); pass through `_polar_to_cartesian` to get SVG
    coords. For e=0 this matches the prior circular approximation
    bit-for-bit, so existing eccentricity=0 fixtures don't drift.
    """
    if body.parent is None:
        return (0.0, 0.0)
    assert body.semi_major_au is not None
    assert body.period_days is not None
    assert body.epoch_phase_deg is not None

    a = body.semi_major_au
    e = body.eccentricity
    t_days = t_hours / 24.0

    # Mean anomaly at time t (radians).
    mean_anomaly = math.radians(body.epoch_phase_deg) + 2 * math.pi * t_days / body.period_days
    mean_anomaly %= 2 * math.pi

    # Eccentric anomaly via Newton's method on M = E - e·sin(E).
    eccentric_anomaly = _solve_kepler(mean_anomaly, e)

    # True anomaly from eccentric anomaly.
    true_anomaly = 2.0 * math.atan2(
        math.sqrt(1.0 + e) * math.sin(eccentric_anomaly / 2.0),
        math.sqrt(1.0 - e) * math.cos(eccentric_anomaly / 2.0),
    )

    # Radial distance from focus.
    r = a * (1.0 - e * math.cos(eccentric_anomaly))

    return (r, math.degrees(true_anomaly) % 360.0)


def ellipse_geometry(body: BodyDef, scale: float) -> EllipseSpec:
    """Orbit-ellipse geometry for SVG `<ellipse>` rendering.

    `scale` converts AU → pixels. For circular orbits (e=0) returns rx=ry
    centered on the focus — i.e. behaves identically to a `<circle r=a*scale>`.
    """
    assert body.semi_major_au is not None
    a = body.semi_major_au
    e = body.eccentricity
    semi_major_px = a * scale
    semi_minor_px = a * math.sqrt(max(0.0, 1.0 - e * e)) * scale
    c_px = a * e * scale
    return EllipseSpec(
        center_x_px=-c_px,
        center_y_px=0.0,
        semi_major_px=semi_major_px,
        semi_minor_px=semi_minor_px,
    )


def moon_kepler_offset(
    moon: BodyDef,
    parent_pos_px: tuple[float, float],
    t_hours: float,
    scale: float,
) -> tuple[float, float]:
    """Moon SVG-pixel position when the chart is centered on the system root
    (not on the moon's parent). The moon's orbit is around its parent, so we
    add the parent's chart position to the moon's local Kepler offset.
    """
    r_au, theta_deg = kepler_position(moon, t_hours)
    rad = math.radians(theta_deg)
    dx = r_au * scale * math.cos(rad)
    dy = -r_au * scale * math.sin(rad)
    return (parent_pos_px[0] + dx, parent_pos_px[1] + dy)


def lagrange_position(
    parent_pos_px: tuple[float, float],
    parent_orbit_radius_au: float,
    point: str,
    scale: float,
) -> tuple[float, float]:
    """Geometric placement of a Lagrange point relative to a parent body.

    `point` is "L1" (between star and parent), "L4" (60° leading), or
    "L5" (60° trailing). No stability math — just geometric placement
    for chart annotation. Caller passes parent's chart position so the
    point appears at the right location regardless of scope.
    """
    parent_x, parent_y = parent_pos_px
    # Vector from origin (star) to parent
    parent_angle = math.atan2(-parent_y, parent_x)  # SVG y-flip
    if point == "L1":
        # Inner Lagrange — about 0.01 AU sunward from parent for typical mass
        # ratios; for chart purposes draw at a small fraction of orbit radius.
        offset_au = parent_orbit_radius_au * 0.05
        x = parent_x - offset_au * scale * math.cos(parent_angle)
        y = parent_y + offset_au * scale * math.sin(parent_angle)
        return (x, y)
    if point == "L4":
        # 60° leading
        leading_angle = parent_angle + math.radians(60)
        x = parent_orbit_radius_au * scale * math.cos(leading_angle)
        y = -parent_orbit_radius_au * scale * math.sin(leading_angle)
        return (x, y)
    if point == "L5":
        # 60° trailing
        trailing_angle = parent_angle - math.radians(60)
        x = parent_orbit_radius_au * scale * math.cos(trailing_angle)
        y = -parent_orbit_radius_au * scale * math.sin(trailing_angle)
        return (x, y)
    raise ValueError(f"Lagrange point must be one of L1/L4/L5; got {point!r}")


def _solve_kepler(mean_anomaly: float, eccentricity: float) -> float:
    """Newton's method on Kepler's equation M = E - e·sin(E).

    Returns E (eccentric anomaly) in radians. Bails on the iteration
    cap rather than looping forever; the cap is generous (~8 iterations)
    for any e < 0.99 with the M=E starting guess.
    """
    eccentric_anomaly = mean_anomaly
    for _ in range(_NEWTON_MAX_ITER):
        residual = eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly
        derivative = 1.0 - eccentricity * math.cos(eccentric_anomaly)
        delta = residual / derivative
        eccentric_anomaly -= delta
        if abs(delta) < _NEWTON_TOL:
            break
    return eccentric_anomaly
