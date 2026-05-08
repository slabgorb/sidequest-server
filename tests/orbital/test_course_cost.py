"""Cost model — Hohmann-flavored, not Hohmann-accurate.

Calibration targets per the plot-a-course design:
- Far Landing → Tethys Watch (small moon hop) ≈ 12h, Δv 0.4
- Far Landing → Deep Root (cross-system rocky) ≈ 30h, Δv 1.0
- Far Landing → The Gate (far-edge habitat) ≈ 90h, Δv 2.8

Numbers are tunable via travel.travel_speed_factor. These tests
lock in the *order of magnitude* and the relative ordering, not
exact decimals — the calibration is allowed to drift within ±15%.
"""

from __future__ import annotations

import pytest

from sidequest.orbital.course import compute_eta_and_dv
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


def _orbits(*bodies: tuple[str, BodyDef], travel_speed_factor: float = 1.0) -> OrbitsConfig:
    return OrbitsConfig(
        version="0.1.0",
        clock=ClockConfig(),
        travel=TravelConfig(
            realism=TravelRealism.ORBITAL,
            travel_speed_factor=travel_speed_factor,
        ),
        bodies=dict(bodies),
    )


def _body(
    type_: BodyType = BodyType.HABITAT,
    parent: str | None = None,
    semi_major_au: float | None = 1.0,
    period_days: float | None = 365.0,
    epoch_phase_deg: float | None = 0.0,
) -> BodyDef:
    return BodyDef(
        type=type_,
        parent=parent,
        semi_major_au=semi_major_au,
        period_days=period_days,
        epoch_phase_deg=epoch_phase_deg,
    )


def test_eta_zero_when_same_body() -> None:
    far = _body(semi_major_au=1.0, epoch_phase_deg=45.0)
    eta, dv = compute_eta_and_dv(far, far, _orbits(("far", far)))
    assert eta == 0.0
    assert dv == 0.0


def test_eta_for_short_radial_hop_is_under_30h() -> None:
    # Tethys Watch is a moon — same parent, near-zero radial diff
    far = _body(semi_major_au=1.0, epoch_phase_deg=45.0)
    moon = _body(parent="far", semi_major_au=1.0039, epoch_phase_deg=45.0)
    eta, dv = compute_eta_and_dv(far, moon, _orbits(("far", far), ("moon", moon)))
    assert eta < 30.0
    assert 0.0 < dv < 1.0


def test_eta_scales_inversely_with_travel_speed_factor() -> None:
    a = _body(semi_major_au=1.0, epoch_phase_deg=0.0)
    b = _body(semi_major_au=2.0, epoch_phase_deg=180.0)
    slow_orbits = _orbits(("a", a), ("b", b), travel_speed_factor=1.0)
    fast_orbits = _orbits(("a", a), ("b", b), travel_speed_factor=2.0)
    eta_slow, _ = compute_eta_and_dv(a, b, slow_orbits)
    eta_fast, _ = compute_eta_and_dv(a, b, fast_orbits)
    assert eta_fast == pytest.approx(eta_slow / 2.0)


def test_dv_independent_of_travel_speed_factor() -> None:
    a = _body(semi_major_au=1.0, epoch_phase_deg=0.0)
    b = _body(semi_major_au=2.0, epoch_phase_deg=180.0)
    _, dv_slow = compute_eta_and_dv(a, b, _orbits(("a", a), ("b", b), travel_speed_factor=1.0))
    _, dv_fast = compute_eta_and_dv(a, b, _orbits(("a", a), ("b", b), travel_speed_factor=2.0))
    assert dv_slow == pytest.approx(dv_fast)


def test_far_to_gate_is_expensive() -> None:
    # Calibration target: Far Landing 1.0 AU, The Gate 4.0 AU edge
    far = _body(semi_major_au=1.0, epoch_phase_deg=45.0)
    gate = _body(type_=BodyType.GATE, semi_major_au=4.0, epoch_phase_deg=180.0)
    eta, dv = compute_eta_and_dv(far, gate, _orbits(("far", far), ("gate", gate)))
    assert 60.0 < eta < 130.0  # ≈ 90h ± headroom
    assert 2.0 < dv < 4.0  # ≈ 2.8 ± headroom
