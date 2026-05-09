"""Geometry helpers for course rendering — pure math, no SideQuest deps."""

from __future__ import annotations

import pytest

from sidequest.orbital.course_geometry import (
    bezier_control_offset,
    chord_angular_distance_deg,
    prograde_sign,
)


def test_chord_angular_distance_zero_when_same_phase() -> None:
    assert chord_angular_distance_deg(0.0, 0.0) == 0.0


def test_chord_angular_distance_180_for_opposite_phase() -> None:
    assert chord_angular_distance_deg(0.0, 180.0) == pytest.approx(180.0)


def test_chord_angular_distance_takes_short_arc() -> None:
    # 350° -> 10° is a 20° short arc, not a 340° long arc
    assert chord_angular_distance_deg(350.0, 10.0) == pytest.approx(20.0)


def test_chord_angular_distance_symmetric() -> None:
    assert chord_angular_distance_deg(45.0, 270.0) == chord_angular_distance_deg(270.0, 45.0)


def test_prograde_sign_destination_ahead_returns_plus_one() -> None:
    # destination 90° prograde of party
    assert prograde_sign(0.0, 90.0) == 1


def test_prograde_sign_destination_behind_returns_minus_one() -> None:
    # destination 90° retrograde (270° prograde, > 180)
    assert prograde_sign(0.0, 270.0) == -1


def test_prograde_sign_diametric_picks_prograde() -> None:
    # exactly 180°: tie-breaks to +1 (prograde) by the ≤ 180 condition
    assert prograde_sign(0.0, 180.0) == 1


def test_bezier_control_offset_scales_with_chord_and_sign() -> None:
    assert bezier_control_offset(100.0, 1) == pytest.approx(30.0)
    assert bezier_control_offset(100.0, -1) == pytest.approx(-30.0)
    assert bezier_control_offset(0.0, 1) == 0.0
