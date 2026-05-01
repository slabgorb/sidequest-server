"""Regression tests for playtest 2026-04-30 — Mira/Dungeon Survivor
chargen-complete with no `magic.init` log.

Pre-fix flow:
1. Player starts a fresh `caverns_and_claudes` / `dungeon_survivor`
   session.
2. Walks chargen (bone-dice stats, Delver class, opening, confirmation).
3. Clicks Create Character. Server log shows `chargen.starting_equipment`
   → `chargen.complete` with NO `magic.init` line in between (compare
   `space_opera`/`coyote_star` which logs `magic.init world=...
   plugins=['innate_v1', 'item_legacy_v1'] bars=4`).
4. Mira shows `Edge 10/10` and `No abilities.` in the UI — same surface
   as space_opera, no item-based magic differentiation.

Root cause:
- `init_magic_state_for_session` requires BOTH the genre-level
  `magic.yaml` AND `worlds/<world>/magic.yaml` to exist; absence at
  either tier silently returns False and skips magic init entirely.
- `caverns_and_claudes` had a draft genre `magic.yaml` in a
  non-canonical schema (everything nested under a `magic:` block,
  with `allowed_sources` carrying per-source plugin attribution).
  Even if the world yaml had existed, the loader would have rejected
  the genre yaml's shape.
- `caverns_and_claudes/worlds/dungeon_survivor/` had NO `magic.yaml`
  file at all — the immediate bug.

Fix (content-only):
- Migrated `caverns_and_claudes/magic.yaml` to the canonical schema
  (matching `space_opera/magic.yaml` + `tests/magic/fixtures/
  space_opera_magic.yaml`). Original draft notes preserved as
  comments for future authoring reference.
- Created `caverns_and_claudes/worlds/dungeon_survivor/magic.yaml`
  honoring the genre defaults (folkloric world_knowledge,
  item_legacy_v1 plugin, no character/world-scope bars — magic in
  C&C lives in items, instantiated by the plugin on pickup).

These tests verify the loader accepts both files end-to-end via
`init_magic_state_for_session` (the chargen-complete entry point) so
the bug doesn't regress on a future schema migration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.genre.magic_loader import load_world_magic
from sidequest.server.magic_init import init_magic_state_for_session

CONTENT_ROOT = (
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
)
CC_PACK = CONTENT_ROOT / "caverns_and_claudes"
SO_PACK = CONTENT_ROOT / "space_opera"


@pytest.fixture
def cc_pack_dir():
    if not CC_PACK.is_dir():
        pytest.skip("caverns_and_claudes content pack not found")
    return CC_PACK


@pytest.fixture
def so_pack_dir():
    if not SO_PACK.is_dir():
        pytest.skip("space_opera content pack not found")
    return SO_PACK


def test_caverns_and_claudes_genre_magic_yaml_loads_without_loader_error(cc_pack_dir):
    """The genre-tier yaml must parse via the canonical schema. Pre-fix
    it was nested under a `magic:` block and wouldn't have parsed even
    if the world yaml existed.
    """
    genre_yaml = cc_pack_dir / "magic.yaml"
    world_yaml = cc_pack_dir / "worlds" / "dungeon_survivor" / "magic.yaml"
    assert genre_yaml.exists(), "C&C genre magic.yaml must exist"
    assert world_yaml.exists(), (
        "C&C dungeon_survivor world magic.yaml must exist — "
        "init_magic_state_for_session requires both tiers"
    )
    # Should not raise.
    config = load_world_magic(genre_yaml=genre_yaml, world_yaml=world_yaml)
    assert config.world_slug == "dungeon_survivor"
    assert config.genre_slug == "caverns_and_claudes"


def test_caverns_and_claudes_dungeon_survivor_magic_init_fires(cc_pack_dir):
    """End-to-end: `init_magic_state_for_session` returns True for
    Mira's session shape and populates `snapshot.magic_state` with
    the item_legacy_v1 plugin active.

    The space_opera comparison test (below) and this test must both
    pass — fixing one shouldn't regress the other.
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="dungeon_survivor",
        turn_manager=TurnManager(),
    )
    result = init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="dungeon_survivor",
        character_id="Mira",
    )
    assert result is True, (
        "init_magic_state_for_session must return True for "
        "caverns_and_claudes/dungeon_survivor — pre-fix returned "
        "False because the world yaml was missing"
    )
    assert snapshot.magic_state is not None
    assert snapshot.magic_state.config.world_slug == "dungeon_survivor"
    assert snapshot.magic_state.config.genre_slug == "caverns_and_claudes"
    assert "item_legacy_v1" in snapshot.magic_state.config.active_plugins


def test_caverns_and_claudes_magic_state_has_no_character_or_world_bars(cc_pack_dir):
    """C&C magic is purely item-based. The world load should produce a
    MagicState with an empty ledger — bars only get instantiated when
    items enter play via item_legacy_v1's add_item path.
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="dungeon_survivor",
        turn_manager=TurnManager(),
    )
    init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="dungeon_survivor",
        character_id="Mira",
    )
    assert snapshot.magic_state.ledger == {}, (
        "C&C ships zero world/character-scope bars; the ledger must be "
        "empty until an item is picked up"
    )


def test_caverns_and_claudes_intensity_and_world_knowledge(cc_pack_dir):
    """Smoke: the migration preserved C&C's authored values (intensity
    0.3, folkloric world-knowledge) — guards against accidental shape
    drift if the yaml is touched again.
    """
    config = load_world_magic(
        genre_yaml=cc_pack_dir / "magic.yaml",
        world_yaml=cc_pack_dir / "worlds" / "dungeon_survivor" / "magic.yaml",
    )
    assert config.intensity == 0.3
    assert config.world_knowledge.primary == "folkloric"
    assert "components" in config.cost_types
    assert "backlash" in config.cost_types
    # Hard limits authored as five entries — guard count + a known id.
    assert len(config.hard_limits) == 5
    limit_ids = {hl.id for hl in config.hard_limits}
    assert "no_resurrection" in limit_ids


def test_space_opera_magic_init_still_fires(so_pack_dir):
    """Companion: ensure the C&C migration didn't accidentally regress
    the space_opera path. The Coyote Star world must load the four
    authored bars (sanity / notice / vitality / hegemony_heat).
    """
    world_slug = "coyote_star"
    assert (so_pack_dir / "worlds" / world_slug / "magic.yaml").is_file(), (
        f"coyote_star magic.yaml missing under {so_pack_dir / 'worlds'}"
    )
    snapshot = GameSnapshot(
        genre_slug="space_opera",
        world_slug=world_slug,
        turn_manager=TurnManager(),
    )
    result = init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=so_pack_dir,
        world_slug=world_slug,
        character_id="Parsley",
    )
    assert result is True
    assert snapshot.magic_state is not None
    # Coyote Star: 1 world-scope bar (hegemony_heat) + 3 character-
    # scope bars (sanity/notice/vitality) seeded for Parsley.
    assert len(snapshot.magic_state.ledger) == 4
