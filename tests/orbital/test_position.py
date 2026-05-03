"""Tests for the eccentric Keplerian position math.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §9.

Pins:
  - circular-orbit regression (e=0 matches the prior formula bit-for-bit)
  - perihelion / aphelion exact positions for known eccentric orbits
  - Newton solver convergence
  - period closure (position at t=period == position at t=0)
  - ellipse_geometry: focus offset and semi-minor formula
"""

from __future__ import annotations

import math

import pytest

from sidequest.orbital.models import BodyDef, BodyType
from sidequest.orbital.position import (
    EllipseSpec,
    _solve_kepler,
    ellipse_geometry,
    kepler_position,
    lagrange_position,
    moon_kepler_offset,
)

# ---------------------------------------------------------------------------
# kepler_position
# ---------------------------------------------------------------------------


def _body(
    *,
    semi_major_au: float = 1.0,
    period_days: float = 365.0,
    epoch_phase_deg: float = 0.0,
    eccentricity: float = 0.0,
) -> BodyDef:
    return BodyDef(
        type=BodyType.HABITAT,
        parent="sun",
        semi_major_au=semi_major_au,
        period_days=period_days,
        epoch_phase_deg=epoch_phase_deg,
        eccentricity=eccentricity,
    )


def test_circular_orbit_at_t0_matches_legacy_formula():
    """For e=0, kepler_position must agree with the prior circular formula.

    Prior formula: theta = (epoch_phase + 360 * t_days / period) % 360,
    radius = semi_major. Pinning this regression so existing eccentricity=0
    snapshots don't drift when commit 2 lands.
    """
    body = _body(epoch_phase_deg=45.0)
    r, theta = kepler_position(body, t_hours=0.0)
    assert r == pytest.approx(1.0)
    assert theta == pytest.approx(45.0)


def test_circular_orbit_advances_linearly():
    """For e=0, t=period/4 should advance position by 90°."""
    body = _body(period_days=400.0, epoch_phase_deg=0.0)
    _, theta = kepler_position(body, t_hours=24.0 * 100.0)  # quarter-period
    assert theta == pytest.approx(90.0)


def test_eccentric_orbit_at_periapsis():
    """At t=0 with epoch_phase=0, body is at periapsis: r=a(1-e), ν=0."""
    body = _body(eccentricity=0.5, epoch_phase_deg=0.0)
    r, theta = kepler_position(body, t_hours=0.0)
    assert r == pytest.approx(0.5)  # a(1-e) = 1 * 0.5
    assert theta == pytest.approx(0.0, abs=1e-4)


def test_eccentric_orbit_at_aphelion():
    """Half a period later, body is at aphelion: r=a(1+e), ν=180°."""
    body = _body(period_days=400.0, eccentricity=0.5, epoch_phase_deg=0.0)
    r, theta = kepler_position(body, t_hours=24.0 * 200.0)  # half period
    assert r == pytest.approx(1.5)  # a(1+e) = 1 * 1.5
    assert theta == pytest.approx(180.0, abs=1e-3)


def test_position_periodic_at_full_period():
    """Position at t=period equals position at t=0."""
    body = _body(period_days=300.0, eccentricity=0.3, epoch_phase_deg=72.0)
    r0, th0 = kepler_position(body, t_hours=0.0)
    r1, th1 = kepler_position(body, t_hours=24.0 * 300.0)
    assert r1 == pytest.approx(r0, abs=1e-5)
    assert th1 == pytest.approx(th0, abs=1e-3)


def test_parentless_body_at_origin():
    """Star bodies (no parent) sit at origin regardless of t."""
    star = BodyDef(type=BodyType.STAR, label="SUN")
    r, theta = kepler_position(star, t_hours=12345.0)
    assert (r, theta) == (0.0, 0.0)


@pytest.mark.parametrize("eccentricity", [0.0, 0.1, 0.3, 0.6, 0.9])
def test_solver_converges_for_a_range_of_eccentricities(eccentricity):
    """Newton solver returns within tolerance across the realistic e-range
    (real orbits in the slice are e<0.1, but we want to know the math
    doesn't fall over if a content author writes a comet-like body)."""
    for mean_anomaly in (0.0, math.pi / 6, math.pi / 2, math.pi, 1.5 * math.pi):
        eccentric = _solve_kepler(mean_anomaly, eccentricity)
        residual = eccentric - eccentricity * math.sin(eccentric) - mean_anomaly
        assert abs(residual) < 1e-5, (
            f"solver failed: e={eccentricity}, M={mean_anomaly}, residual={residual}"
        )


# ---------------------------------------------------------------------------
# ellipse_geometry
# ---------------------------------------------------------------------------


def test_ellipse_geometry_circular_is_centered_circle():
    """For e=0, ellipse degenerates to a centered circle (rx=ry, center=focus)."""
    body = _body(eccentricity=0.0)
    spec = ellipse_geometry(body, scale=100.0)
    assert isinstance(spec, EllipseSpec)
    assert spec.center_x_px == pytest.approx(0.0)
    assert spec.center_y_px == pytest.approx(0.0)
    assert spec.semi_major_px == pytest.approx(100.0)
    assert spec.semi_minor_px == pytest.approx(100.0)


def test_ellipse_geometry_eccentric_offsets_center_and_squashes_minor_axis():
    """For e=0.5, semi-minor = a·√(1-0.25) = a·√0.75; center offset by -a·e."""
    body = _body(semi_major_au=2.0, eccentricity=0.5)
    spec = ellipse_geometry(body, scale=100.0)
    assert spec.center_x_px == pytest.approx(-100.0)  # -a·e·scale = -2·0.5·100
    assert spec.center_y_px == pytest.approx(0.0)
    assert spec.semi_major_px == pytest.approx(200.0)
    assert spec.semi_minor_px == pytest.approx(200.0 * math.sqrt(0.75))


def test_body_lies_on_its_orbit_ellipse():
    """The body's Kepler position must satisfy the ellipse equation
    (x - cx)² / rx² + (y - cy)² / ry² = 1. Sanity check that the two
    derivations are mutually consistent."""
    body = _body(semi_major_au=1.5, eccentricity=0.4, epoch_phase_deg=37.0)
    spec = ellipse_geometry(body, scale=1.0)
    for t_hours in (0.0, 100.0, 200.0, 1000.0):
        r, theta = kepler_position(body, t_hours)
        x = r * math.cos(math.radians(theta))
        y = r * math.sin(math.radians(theta))
        ratio = (x - spec.center_x_px) ** 2 / spec.semi_major_px**2 + (
            y - spec.center_y_px
        ) ** 2 / spec.semi_minor_px**2
        assert ratio == pytest.approx(1.0, abs=1e-5), (
            f"body off its ellipse at t={t_hours}: ratio={ratio}"
        )


# ---------------------------------------------------------------------------
# moon_kepler_offset
# ---------------------------------------------------------------------------


def test_moon_offset_adds_to_parent_position():
    """A moon at periapsis (r=a(1-e), ν=0) sits at parent_pos + (a(1-e)*scale, 0)."""
    moon = _body(semi_major_au=0.05, eccentricity=0.0, epoch_phase_deg=0.0)
    parent_pos = (200.0, -150.0)
    x, y = moon_kepler_offset(moon, parent_pos, t_hours=0.0, scale=100.0)
    assert x == pytest.approx(200.0 + 5.0)  # 0.05 * 100 = 5px to the right
    assert y == pytest.approx(-150.0)


# ---------------------------------------------------------------------------
# lagrange_position
# ---------------------------------------------------------------------------


def test_lagrange_l4_60_degrees_leading():
    """L4 leads the parent by 60° on the parent's orbit."""
    parent_pos = (100.0, 0.0)  # parent at 0° on its orbit
    x, y = lagrange_position(parent_pos, parent_orbit_radius_au=1.0, point="L4", scale=100.0)
    # Leading by 60° means at angle 60° on the same circle of radius 100px
    expected_x = 100.0 * math.cos(math.radians(60))
    expected_y = -100.0 * math.sin(math.radians(60))
    assert x == pytest.approx(expected_x, abs=1e-6)
    assert y == pytest.approx(expected_y, abs=1e-6)


def test_lagrange_l5_60_degrees_trailing():
    parent_pos = (100.0, 0.0)
    x, _ = lagrange_position(parent_pos, parent_orbit_radius_au=1.0, point="L5", scale=100.0)
    # Trailing — angle -60° from parent
    assert x == pytest.approx(100.0 * math.cos(math.radians(-60)), abs=1e-6)


def test_lagrange_unknown_point_raises():
    with pytest.raises(ValueError, match="L1/L4/L5"):
        lagrange_position((0.0, 0.0), 1.0, "L7", 100.0)
