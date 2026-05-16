"""Wandering table = curated ∩ race.filter ∩ cr_band, with telegraph.
Loot table = items ∩ rarity_by_band[band] with race.loot_bias applied."""

from __future__ import annotations

from sidequest.game.cookbook.assemble import (
    band_for_depth,
    build_loot_table,
    build_wandering_table,
    region_rng,
)


def _undead(bundle):
    return next(r for r in bundle.races if r.id == "undead")


def test_wandering_rows_in_band_and_have_telegraph(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.40)  # mid
    rows = build_wandering_table(bundle, _undead(bundle), band)
    assert rows, "undead must resolve ≥1 row at mid"
    for row in rows:
        assert band.cr_min <= row["cr"] <= band.cr_max
        assert row["telegraph"] == _undead(bundle).telegraph["mid"]
        assert "weight" in row and "count" in row


def test_loot_table_respects_rarity_band_and_bias(bundle) -> None:
    band = band_for_depth(bundle.affinities, 0.95)  # deep
    rng = region_rng("c", "e")
    loot = build_loot_table(bundle, _undead(bundle), band, rolls=4, rng=rng)
    assert len(loot) == 4
    allowed = set(bundle.affinities.rarity_by_band["deep"])
    for item in loot:
        assert item["rarity"] in allowed


def test_deep_loot_actually_surfaces_very_rare_tier(bundle) -> None:
    # Lie-detector: the plan's weak assertion above passes even if a
    # whole tier is silently dropped by a corpus/affinity casing
    # mismatch. The deep band weights 'Very rare' at 3 and the corpus
    # has 49 such items — over many rolls it MUST appear, else a tier
    # is being silently excluded (spec §7: no silent empty/partial).
    band = band_for_depth(bundle.affinities, 0.95)
    seen = set()
    for i in range(2000):
        rng = region_rng("camp", f"loot-{i}")
        for it in build_loot_table(bundle, _undead(bundle), band, rolls=4, rng=rng):
            seen.add(it["rarity"])
    assert "Very rare" in seen, (
        f"deep loot never surfaced the 'Very rare' tier — silent exclusion. saw: {sorted(seen)}"
    )
