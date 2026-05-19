"""Spec §9: across a seed sweep no denied type/tag/name appears in any
assembled manifest; every marquee survives curation."""

from __future__ import annotations

from sidequest.game.cookbook import assemble_region
from sidequest.game.cookbook.curation import apply_world_register


def test_no_denied_row_in_any_manifest(bundle) -> None:
    deny_types = set(bundle.register.deny.types)
    deny_tags = set(bundle.register.deny.tags)
    marquee = set(bundle.register.marquee)
    by_name = {m.name: m for m in bundle.monsters}
    looks = [lk.id for lk in bundle.looks]
    for i in range(1500):
        look = looks[i % len(looks)]
        man = assemble_region(
            bundle,
            campaign_seed="sweep",
            expansion_id=f"e{i}",
            depth_score=(i % 100) / 100.0,
            burst_magnitude=(i % 9) + 1,
            look=look,
            is_first_band_entry=(i % 7 == 0),
            room_id=f"sweep_region_{i}",
        )
        for row in man.wandering_table:
            mon = by_name.get(row["name"])
            if mon is None or mon.name in marquee:
                continue
            assert mon.type not in deny_types
            assert not (deny_tags & set(mon.tags))


def test_marquee_survives_curation(bundle) -> None:
    curated = {m.name for m in apply_world_register(bundle.monsters, bundle.register)}
    for name in bundle.register.marquee:
        if any(m.name == name for m in bundle.monsters):
            assert name in curated, f"marquee '{name}' wrongly curated out"
