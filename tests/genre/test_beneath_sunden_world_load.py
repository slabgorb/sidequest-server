"""Wiring + validity test for the beneath_sunden world artifact (Plan 8).

Coordinated with oq-1 per cookbook spec §9: asserts the genre loader
discovers beneath_sunden as a valid World — not engine behavior.

Plan-vs-loader divergence (Plan 8 Task 2): the World model has no
`hamlet`/`rooms` fields. The surface anchor is authored against the real
loader contract — world.yaml, lore.yaml, cartography.yaml, openings.yaml —
so these assertions read the measured `World` model (config / lore /
cartography / openings), never invented fields.
"""

from pathlib import Path

import pytest

from sidequest.genre.loader import load_genre_pack

PACK = Path(__file__).resolve().parents[3] / (
    "sidequest-content/genre_packs/caverns_and_claudes"
)

# Identity tokens that belong to caverns_sunden's three-sins hub. The
# beneath_sunden anchor is ONE tragic deep-delve (Moria-as-tragedy) and
# must not reuse them.
_CAVERNS_SUNDEN_TOKENS = (
    "grimvault",
    "horden",
    "mawdeep",
    "three sins",
    "seven deadly sins",
    "patient butcher",
)


@pytest.fixture(scope="module")
def pack():
    return load_genre_pack(PACK)


def test_beneath_sunden_discovered_as_world(pack):
    assert "beneath_sunden" in pack.worlds, (
        f"beneath_sunden not discovered; worlds={sorted(pack.worlds)}"
    )


def test_beneath_sunden_identity_is_moria_grave(pack):
    w = pack.worlds["beneath_sunden"].config
    assert w.name
    assert w.description
    # world_register.yaml fixes gravity >= 0.85 (Moria-as-tragedy, no winking).
    assert w.axis_snapshot.get("gravity", 0.0) >= 0.85, (
        f"gravity must be >= 0.85 per world_register; got {w.axis_snapshot}"
    )
    # Played straight: comedy stays low (spec / ADR-106 tone axes).
    assert w.axis_snapshot.get("comedy", 1.0) <= 0.10, (
        f"comedy must be <= 0.10 (played straight); got {w.axis_snapshot}"
    )


def test_beneath_sunden_lore_is_its_own_identity(pack):
    """lore.yaml is REQUIRED by _load_single_world and must carry the
    world's OWN grave identity, not caverns_sunden's three-sins hub."""
    lore = pack.worlds["beneath_sunden"].lore
    blob = " ".join(
        str(x or "").lower()
        for x in (lore.world_name, lore.history, lore.geography, lore.cosmology)
    )
    assert blob.strip(), "beneath_sunden lore.yaml must carry real prose, not be empty"
    for token in _CAVERNS_SUNDEN_TOKENS:
        assert token not in blob, (
            f"beneath_sunden lore must not reuse caverns_sunden identity: {token!r}"
        )


def test_beneath_sunden_cartography_has_descent_and_settlement(pack):
    """The surface anchor lives in cartography.yaml (the real loader
    contract — there is no hamlet.yaml/rooms/ in the World model). The
    descent region is the documented Plan-7 handoff seam."""
    carto = pack.worlds["beneath_sunden"].cartography
    region_ids = set(carto.regions)
    assert "the_dropmouth" in region_ids, (
        f"descent region 'the_dropmouth' (cover_poi) must exist; got {sorted(region_ids)}"
    )
    # starting_region must resolve to an authored region (no dangling seed).
    assert carto.starting_region in region_ids, (
        f"starting_region {carto.starting_region!r} must resolve to an "
        f"authored region; got {sorted(region_ids)}"
    )
    # navigation_mode stays 'region' — keeps the room_graph runtime lane
    # (Plans 5-7, the procedural deep) cleanly out of this static anchor.
    assert carto.navigation_mode == "region", (
        f"surface anchor must stay navigation_mode=region (procedural deep "
        f"is the fenced runtime lane); got {carto.navigation_mode!r}"
    )
    # At least one non-descent settlement region (the waiting-place).
    assert len(region_ids) >= 2, (
        f"expected the descent region plus a surface settlement; got {sorted(region_ids)}"
    )
    # Identity guard on cartography prose.
    carto_blob = " ".join(
        f"{r.name} {r.summary} {r.description}".lower()
        for r in carto.regions.values()
    )
    for token in _CAVERNS_SUNDEN_TOKENS:
        assert token not in carto_blob, (
            f"beneath_sunden cartography must not reuse caverns_sunden identity: {token!r}"
        )


def test_beneath_sunden_openings_cover_solo_and_mp(pack):
    """openings.yaml is MANDATORY (_load_openings) and Validator 7 requires
    >=1 solo-eligible AND >=1 MP-eligible opening. If the pack loaded at
    all these hold, but assert explicitly so the wiring is self-documenting.
    """
    openings = pack.worlds["beneath_sunden"].openings
    assert openings, "beneath_sunden must author at least one opening"
    has_solo = any(o.triggers.mode in ("solo", "either") for o in openings)
    has_mp = any(o.triggers.mode in ("multiplayer", "either") for o in openings)
    assert has_solo, f"need >=1 solo-eligible opening; modes={[o.triggers.mode for o in openings]}"
    assert has_mp, f"need >=1 MP-eligible opening; modes={[o.triggers.mode for o in openings]}"
