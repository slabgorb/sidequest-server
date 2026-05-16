"""Spec §9: LOOK→RACE roll frequencies track configured weights within
tolerance; off-affinity RACEs still appear (orthogonality, not lock)."""

from __future__ import annotations

import collections

from sidequest.game.cookbook.assemble import region_rng, roll_race


def test_distribution_tracks_weights(bundle) -> None:
    weights = bundle.affinities.look_race_affinity["necropolis"]
    total = sum(weights.values())
    counts: collections.Counter[str] = collections.Counter()
    for i in range(8000):
        rng = region_rng("camp", f"exp-{i}")
        counts[roll_race(bundle, "necropolis", rng).id] += 1
    n = sum(counts.values())
    # 'undead' is the dominant affinity — its empirical share must be
    # within 5 points of configured share over 8k draws.
    exp_undead = weights["undead"] / total
    assert abs(counts["undead"] / n - exp_undead) < 0.05


def test_off_affinity_still_appears(bundle) -> None:
    # ooze has weight 1 under necropolis — rare but NOT impossible.
    seen = set()
    for i in range(8000):
        rng = region_rng("camp", f"x-{i}")
        seen.add(roll_race(bundle, "necropolis", rng).id)
    assert "ooze" in seen
