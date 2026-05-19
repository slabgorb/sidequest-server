"""assemble_region composes a complete RegionContentManifest and is a
pure function of its named inputs (spec §4.3)."""

from __future__ import annotations

from sidequest.game.cookbook import assemble_region
from sidequest.game.cookbook.models import RegionContentManifest


def test_manifest_is_complete_and_typed(bundle) -> None:
    man = assemble_region(
        bundle,
        campaign_seed="camp-1",
        expansion_id="exp-3",
        depth_score=0.40,
        burst_magnitude=3,
        look="necropolis",
        is_first_band_entry=True,
        room_id="test_region_1",
    )
    assert isinstance(man, RegionContentManifest)
    assert man.cr_band == "mid"
    assert man.race in {r.id for r in bundle.races}
    assert man.wandering_table  # mid undead/etc resolves ≥1
    assert man.size_budget["wandering_rolls"] >= 1
    # First entry into gated 'mid' → capstone present, forces SIZE ≥ large.
    assert man.big_bad is not None
    largest = bundle.affinities.size_by_burst[-1]
    assert man.size_budget["wandering_rolls"] == largest.wandering_rolls


def test_pure_function_same_inputs_same_manifest(bundle) -> None:
    kw = dict(
        campaign_seed="c",
        expansion_id="e",
        depth_score=0.7,
        burst_magnitude=2,
        look="sunken",
        is_first_band_entry=False,
        room_id="test_region_pure",
    )
    a = assemble_region(bundle, **kw)
    b = assemble_region(bundle, **kw)
    assert a.model_dump() == b.model_dump()


def test_capstone_floors_size(bundle) -> None:
    # deep + first entry → big_bad → SIZE floored to big_bad_forces_size row.
    man = assemble_region(
        bundle,
        campaign_seed="c",
        expansion_id="e2",
        depth_score=0.95,
        burst_magnitude=1,
        look="delvehold",
        is_first_band_entry=True,
        room_id="test_region_capstone",
    )
    assert man.big_bad is not None
    largest = bundle.affinities.size_by_burst[-1]
    assert man.size_budget["wandering_rolls"] == largest.wandering_rolls


def test_low_ceiling_reroll_never_emits_empty_deep_table(bundle) -> None:
    # Data-Forced Design Item: 'sunken' is ooze-heavy, but ooze (CR≤4)
    # cannot fill a deep region. Over many seeds at deep depth, the
    # manifest must NEVER be ooze/goblinoid and NEVER have an empty
    # wandering table — the observable re-roll yields to undead/etc.
    for i in range(400):
        man = assemble_region(
            bundle,
            campaign_seed="reroll",
            expansion_id=f"e{i}",
            depth_score=0.92,
            burst_magnitude=3,
            look="sunken",
            is_first_band_entry=False,
            room_id=f"test_region_reroll_{i}",
        )
        assert man.cr_band == "deep"
        assert man.wandering_table, "deep region must never be empty"
        assert man.race not in {"ooze", "goblinoid"}
