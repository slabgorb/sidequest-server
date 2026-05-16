"""Spec §7/§9: every RACE filter resolves ≥1 curated row in every band it
claims (via big_bads.min_band or LOOK affinity presence). Loud otherwise."""

from __future__ import annotations

import pytest

from sidequest.game.cookbook.loader import CookbookValidationError, validate_bundle


def test_real_bundle_validates(bundle) -> None:
    # Must not raise. Per "Data-Forced Design Item": ooze (ceiling CR 4)
    # and goblinoid (ceiling CR 1) do NOT fill `deep` — and that is
    # explicitly NOT a build error under the corrected semantics.
    validate_bundle(bundle)


def test_shallow_entry_guarantee_is_loud(bundle) -> None:
    # Deny every Undead/Construct → 'undead' empties even at SHALLOW →
    # violates the entry guarantee → must raise naming the RACE.
    reg = bundle.register.model_copy(deep=True)
    reg.deny.types = list(set(reg.deny.types) | {"Undead", "Construct"})
    broken = type(bundle)(
        monsters=bundle.monsters,
        items=bundle.items,
        register=reg,
        races=bundle.races,
        looks=bundle.looks,
        affinities=bundle.affinities,
        specials=bundle.specials,
    )
    with pytest.raises(CookbookValidationError, match="undead"):
        validate_bundle(broken)


def test_unreachable_bigbad_is_loud(bundle) -> None:
    # Give goblinoid a big_bad whose CR can't reach its declared
    # min_band → a declared capstone is unreachable → must raise.
    gob = next(r for r in bundle.races if r.id == "goblinoid")
    bad = gob.model_copy(update={"big_bads": [{"name": "Goblin", "min_band": "deep"}]})
    broken = type(bundle)(
        monsters=bundle.monsters,
        items=bundle.items,
        register=bundle.register,
        races=[bad if r.id == "goblinoid" else r for r in bundle.races],
        looks=bundle.looks,
        affinities=bundle.affinities,
        specials=bundle.specials,
    )
    with pytest.raises(CookbookValidationError, match="goblinoid"):
        validate_bundle(broken)
