"""Cookbook model round-trip + extra-forbid invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.game.cookbook.models import (
    CorpusItem,
    CorpusMonster,
    CrBand,
    RegionContentManifest,
)


def test_corpus_monster_parses() -> None:
    m = CorpusMonster(
        name="Skeleton",
        size="Medium",
        type="Undead",
        tags=[],
        alignment="LE",
        cr=0.25,
        xp=50,
        source="mm 282",
    )
    assert m.cr == 0.25 and m.tags == []


def test_corpus_monster_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        CorpusMonster(
            name="X",
            size="Medium",
            type="Undead",
            tags=[],
            alignment="LE",
            cr=1.0,
            xp=200,
            source="",
            bogus=True,
        )


def test_cr_band_is_ordinal_via_index() -> None:
    bands = [
        CrBand(id="shallow", depth_lt=0.25, cr_min=0, cr_max=2),
        CrBand(id="mid", depth_lt=0.60, cr_min=2, cr_max=7),
        CrBand(id="deep", depth_lt=1.01, cr_min=6, cr_max=30),
    ]
    order = {b.id: i for i, b in enumerate(bands)}
    assert order["shallow"] < order["mid"] < order["deep"]


def test_manifest_minimal() -> None:
    man = RegionContentManifest(
        race="undead",
        cr_band="mid",
        size_budget={"wandering_rolls": 3, "special_rooms": 1, "loot_rolls": 2},
        wandering_table=[],
        loot_table=[],
        special_rooms=[],
        big_bad=None,
    )
    assert man.big_bad is None
    _ = CorpusItem(
        name="Potion of Healing",
        item_type="Potion",
        rarity="Common",
        attunement=False,
        notes="",
        source="dmg 288",
    )
