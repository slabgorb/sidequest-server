"""SPECIAL selection: ≤ size_budget.special_rooms, gated by min_band
(ordinal). Deterministic under fixed rng."""

from __future__ import annotations

from sidequest.game.cookbook.assemble import band_for_depth, pick_specials, region_rng


def test_respects_budget_and_min_band(bundle) -> None:
    shallow = band_for_depth(bundle.affinities, 0.10)
    chosen = pick_specials(bundle, shallow, budget=2, rng=region_rng("c", "e"))
    assert len(chosen) <= 2
    order = bundle.affinities.band_order()
    for s in chosen:
        assert order[s["min_band"]] <= order[shallow.id]


def test_zero_budget_yields_none(bundle) -> None:
    deep = band_for_depth(bundle.affinities, 0.95)
    assert pick_specials(bundle, deep, budget=0, rng=region_rng("c", "e")) == []


def test_deterministic(bundle) -> None:
    deep = band_for_depth(bundle.affinities, 0.95)
    a = pick_specials(bundle, deep, budget=2, rng=region_rng("c", "e"))
    b = pick_specials(bundle, deep, budget=2, rng=region_rng("c", "e"))
    assert a == b
