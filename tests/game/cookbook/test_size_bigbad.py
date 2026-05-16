"""CR band from depth_score; size budget from burst (monotonic, spec §9)."""

from __future__ import annotations

from sidequest.game.cookbook.assemble import band_for_depth, budget_for_burst


def test_band_for_depth(bundle) -> None:
    a = bundle.affinities
    assert band_for_depth(a, 0.10).id == "shallow"
    assert band_for_depth(a, 0.40).id == "mid"
    assert band_for_depth(a, 0.95).id == "deep"


def test_band_boundary_is_lower_inclusive(bundle) -> None:
    # depth_lt is an exclusive upper bound; 0.25 falls into 'mid'.
    assert band_for_depth(bundle.affinities, 0.25).id == "mid"


def test_budget_monotonic_in_burst(bundle) -> None:
    a = bundle.affinities
    b1 = budget_for_burst(a, 1)
    b3 = budget_for_burst(a, 3)
    b9 = budget_for_burst(a, 9)
    assert b1.wandering_rolls <= b3.wandering_rolls <= b9.wandering_rolls
    assert b1.special_rooms <= b3.special_rooms <= b9.special_rooms


def test_burst_above_max_clamps_to_largest(bundle) -> None:
    a = bundle.affinities
    assert budget_for_burst(a, 999).wandering_rolls == budget_for_burst(a, 9).wandering_rolls
