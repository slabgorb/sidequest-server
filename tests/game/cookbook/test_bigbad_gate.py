"""BIG BAD gate (spec §4.2/§4.3): first entry into a gated band → capstone;
else per-band recurring_chance. Capstone pulled from race.big_bads whose
min_band ≤ cr_band (ordinal). is_first_band_entry is an oq-1-supplied input
(see plan 'Seam Clarification')."""

from __future__ import annotations

from sidequest.game.cookbook.assemble import band_for_depth, region_rng, roll_big_bad


def _undead(bundle):
    return next(r for r in bundle.races if r.id == "undead")


def test_first_entry_into_gated_band_forces_capstone(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.40)  # mid (gated)
    bb = roll_big_bad(
        bundle,
        _undead(bundle),
        band,
        is_first_band_entry=True,
        rng=region_rng("c", "e"),
    )
    assert bb is not None
    # undead.yaml (authoritative): Wight is the min_band=mid capstone;
    # Mummy Lord & Lich are min_band=deep (ordinal-excluded at mid).
    assert bb["name"] == "Wight"


def test_shallow_band_never_capstones(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.10)  # shallow (not in gate)
    bb = roll_big_bad(
        bundle,
        _undead(bundle),
        band,
        is_first_band_entry=True,
        rng=region_rng("c", "e"),
    )
    assert bb is None


def test_recurring_chance_is_deterministic_and_bounded(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.95)  # deep
    hits = 0
    for i in range(2000):
        bb = roll_big_bad(
            bundle,
            _undead(bundle),
            band,
            is_first_band_entry=False,
            rng=region_rng("c", f"e{i}"),
        )
        hits += bb is not None
    rate = hits / 2000
    # configured deep recurring_chance is 0.20 — empirical within ±0.05
    assert 0.15 < rate < 0.25


def test_min_band_ordinal_excludes_too_deep_bigbad(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.40)  # mid
    # Over many first-entries, Lich (min_band deep) must NEVER appear at mid.
    for i in range(500):
        bb = roll_big_bad(
            bundle,
            _undead(bundle),
            band,
            is_first_band_entry=True,
            rng=region_rng("c", f"e{i}"),
        )
        assert bb is None or bb["name"] != "Lich"
