"""Hub-world loader tests — caverns_three_sins shape.

A hub world has a populated ``dungeons/`` subdirectory; cartography,
openings, rooms, creatures, and encounter tables move per-dungeon.
Cartography is rejected at the hub level. Each dungeon's ``parent_world``
must equal the hub's slug.

These tests pin the new behavior added in the genre-loader-dungeon-recursion
plan (2026-05-04). Until that plan landed, all 14 ``test_loader.py`` tests
that load ``caverns_and_claudes`` failed at the missing-world-cartography
boundary; they go green together with this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sidequest.genre.error import GenreLoadError
from sidequest.genre.loader import _load_single_world, load_genre_pack

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
CC = CONTENT_ROOT / "caverns_and_claudes"
HUB_SLUG = "caverns_three_sins"
EXPECTED_DUNGEONS = {"grimvault", "horden", "mawdeep"}


def _has_real_content() -> bool:
    return CC.exists()


# ---------------------------------------------------------------------------
# Hub world loads cleanly
# ---------------------------------------------------------------------------


def test_caverns_three_sins_loads_as_hub() -> None:
    """The full pack loads; the hub world is recognized; the three
    expected dungeons are present.
    """
    if not _has_real_content():
        pytest.skip("sidequest-content not available")

    pack = load_genre_pack(CC)
    hub = pack.worlds[HUB_SLUG]

    assert hub.dungeons, "hub world must have dungeons populated"
    assert set(hub.dungeons) == EXPECTED_DUNGEONS, (
        f"expected dungeons {EXPECTED_DUNGEONS}, got {set(hub.dungeons)}"
    )


def test_hub_world_carries_no_world_level_cartography() -> None:
    """Invariant: a hub world's ``cartography`` is None — cartography
    has moved per-dungeon.
    """
    if not _has_real_content():
        pytest.skip("sidequest-content not available")

    pack = load_genre_pack(CC)
    hub = pack.worlds[HUB_SLUG]

    assert hub.cartography is None
    assert hub.openings == [], "hub world must not carry openings either"


def test_each_dungeon_has_cartography_and_parent_link() -> None:
    """Every dungeon owns its own cartography and declares ``parent_world``
    matching the hub slug.
    """
    if not _has_real_content():
        pytest.skip("sidequest-content not available")

    pack = load_genre_pack(CC)
    hub = pack.worlds[HUB_SLUG]

    for slug, dungeon in hub.dungeons.items():
        assert dungeon.cartography is not None, f"{slug}: missing cartography"
        assert dungeon.config.parent_world == HUB_SLUG, (
            f"{slug}: parent_world={dungeon.config.parent_world!r} != {HUB_SLUG!r}"
        )
        assert dungeon.openings, f"{slug}: openings should be populated"


def test_dungeon_sin_tags_present() -> None:
    """Each dungeon carries its sin tag; this is content-side data that the
    later drift / wound subsystems will read.
    """
    if not _has_real_content():
        pytest.skip("sidequest-content not available")

    pack = load_genre_pack(CC)
    hub = pack.worlds[HUB_SLUG]

    expected_sins = {"grimvault": "pride", "horden": "greed", "mawdeep": "gluttony"}
    for slug, expected_sin in expected_sins.items():
        assert hub.dungeons[slug].config.sin == expected_sin, (
            f"{slug}: sin={hub.dungeons[slug].config.sin!r} != {expected_sin!r}"
        )


def test_hamlet_yaml_is_loaded_when_present() -> None:
    """The hub's ``hamlet.yaml`` is loaded as raw YAML (typed schema in
    a later plan).
    """
    if not _has_real_content():
        pytest.skip("sidequest-content not available")

    pack = load_genre_pack(CC)
    hub = pack.worlds[HUB_SLUG]

    if (CC / "worlds" / HUB_SLUG / "hamlet.yaml").exists():
        assert hub.hamlet is not None, "hamlet.yaml exists on disk but didn't load"


# ---------------------------------------------------------------------------
# Leaf worlds in OTHER genre packs are unchanged
# ---------------------------------------------------------------------------


def test_space_opera_leaf_world_unchanged() -> None:
    """Regression: a non-hub genre pack still loads with cartography and
    openings at world level, and an empty ``dungeons`` dict.
    """
    space_opera = CONTENT_ROOT / "space_opera"
    if not space_opera.exists():
        pytest.skip("space_opera content not available")

    pack = load_genre_pack(space_opera)
    assert pack.worlds, "space_opera must have at least one world"

    for world_slug, world in pack.worlds.items():
        assert world.cartography is not None, f"{world_slug}: leaf must have cartography"
        assert world.openings, f"{world_slug}: leaf must have openings"
        assert world.dungeons == {}, f"{world_slug}: leaf must have empty dungeons"


# ---------------------------------------------------------------------------
# Authoring-mistake rejections — fail loud per No Silent Fallbacks
#
# These exercise _load_single_world directly rather than load_genre_pack,
# because we don't need a full minimal genre pack to pin world-level
# loader behavior.
# ---------------------------------------------------------------------------


def _write_world_skeleton(world_dir: Path) -> None:
    """Write the bare world.yaml + lore.yaml that _load_single_world needs."""
    world_dir.mkdir(parents=True, exist_ok=True)
    (world_dir / "world.yaml").write_text(yaml.dump({"name": "Test Hub", "description": "test"}))
    (world_dir / "lore.yaml").write_text(yaml.dump({"world_name": "T"}))


def _write_dungeon(
    dungeon_dir: Path,
    *,
    parent_world: str,
    name: str = "Test Dungeon",
) -> None:
    dungeon_dir.mkdir(parents=True, exist_ok=True)
    (dungeon_dir / "dungeon.yaml").write_text(
        yaml.dump(
            {
                "parent_world": parent_world,
                "name": name,
                "description": "test",
            }
        )
    )
    (dungeon_dir / "cartography.yaml").write_text(
        yaml.dump(
            {
                "navigation_mode": "region",
                "regions": {
                    "r1": {
                        "name": "R1",
                        "summary": "test region",
                        "description": "test description",
                    }
                },
            }
        )
    )
    _opening = {
        "id": "o",
        "name": "Test Opening",
        "triggers": {"mode": "either", "backgrounds": []},
        "setting": {
            "location_label": "test location",
            "situation": "test situation prose",
        },
        "tone": {"register": "neutral", "stakes": "low"},
        "establishing_narration": "test establishing narration prose",
        "first_turn_invitation": "What do you do.",
        "directive": "go",
    }
    (dungeon_dir / "openings.yaml").write_text(yaml.dump({"version": 1, "openings": [_opening]}))


def test_hub_world_with_world_level_cartography_rejected(tmp_path: Path) -> None:
    """Hub world (has dungeons/) cannot also carry world-level cartography.yaml.
    The error message must point at the offending file.
    """
    world_dir = tmp_path / "worlds" / "h"
    _write_world_skeleton(world_dir)
    _write_dungeon(world_dir / "dungeons" / "d", parent_world="h")
    (world_dir / "cartography.yaml").write_text(
        yaml.dump({"navigation_mode": "region", "regions": {}})
    )

    with pytest.raises(GenreLoadError) as exc_info:
        _load_single_world(world_dir, [], tmp_path)

    msg = str(exc_info.value)
    assert "hub world" in msg.lower()
    assert "cartography.yaml" in msg
    assert "dungeons/" in msg


def test_dungeon_with_mismatched_parent_world_rejected(tmp_path: Path) -> None:
    """``dungeon.yaml.parent_world`` must equal the directory name two levels up.
    Mismatch is a loud authoring error.
    """
    world_dir = tmp_path / "worlds" / "h"
    _write_world_skeleton(world_dir)
    _write_dungeon(world_dir / "dungeons" / "d", parent_world="not_h")

    with pytest.raises(GenreLoadError) as exc_info:
        _load_single_world(world_dir, [], tmp_path)

    msg = str(exc_info.value)
    assert "parent_world" in msg
    assert "'not_h'" in msg
    assert "'h'" in msg


def test_dungeon_missing_cartography_rejected(tmp_path: Path) -> None:
    """A dungeon without cartography.yaml is a loud error — every dungeon
    needs its own.
    """
    world_dir = tmp_path / "worlds" / "h"
    _write_world_skeleton(world_dir)
    dungeon_dir = world_dir / "dungeons" / "d"
    _write_dungeon(dungeon_dir, parent_world="h")
    (dungeon_dir / "cartography.yaml").unlink()

    with pytest.raises(GenreLoadError) as exc_info:
        _load_single_world(world_dir, [], tmp_path)

    msg = str(exc_info.value)
    assert "cartography.yaml" in msg


def test_hub_world_with_world_level_openings_rejected(tmp_path: Path) -> None:
    """Hub world cannot carry world-level openings.yaml; openings live
    per-dungeon."""
    world_dir = tmp_path / "worlds" / "h"
    _write_world_skeleton(world_dir)
    _write_dungeon(world_dir / "dungeons" / "d", parent_world="h")
    (world_dir / "openings.yaml").write_text(yaml.dump({"openings": []}))

    with pytest.raises(GenreLoadError) as exc_info:
        _load_single_world(world_dir, [], tmp_path)

    msg = str(exc_info.value)
    assert "hub world" in msg.lower()
    assert "openings.yaml" in msg


def test_hub_world_loads_via_load_single_world(tmp_path: Path) -> None:
    """Wiring spot-check: a synthetic hub directory loads through
    ``_load_single_world`` and produces a populated ``dungeons`` dict."""
    world_dir = tmp_path / "worlds" / "h"
    _write_world_skeleton(world_dir)
    _write_dungeon(world_dir / "dungeons" / "d1", parent_world="h", name="One")
    _write_dungeon(world_dir / "dungeons" / "d2", parent_world="h", name="Two")

    world = _load_single_world(world_dir, [], tmp_path)

    assert world.cartography is None
    assert world.openings == []
    assert set(world.dungeons) == {"d1", "d2"}
    for _slug, dungeon in world.dungeons.items():
        assert dungeon.cartography is not None
        assert dungeon.config.parent_world == "h"
        assert len(dungeon.openings) == 1
