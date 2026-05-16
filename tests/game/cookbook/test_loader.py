"""Loader wires the real authored YAML into typed models."""

from __future__ import annotations


def test_bundle_has_all_axes(bundle) -> None:
    assert {r.id for r in bundle.races} >= {"undead", "aberration", "ooze", "goblinoid", "dwarf"}
    assert {lk.id for lk in bundle.looks} == {"necropolis", "sunken", "delvehold"}
    assert [b.id for b in bundle.affinities.cr_bands] == ["shallow", "mid", "deep"]
    assert bundle.register.allow_types
    assert len(bundle.monsters) > 0 and len(bundle.items) > 0
    assert {s.id for s in bundle.specials} >= {"teleporter_room"}
