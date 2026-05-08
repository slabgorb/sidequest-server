"""Regression tests for playtest 2026-04-30 — Mira/Caverns Sünden
chargen-complete with no `magic.init` log.

Pre-fix flow:
1. Player starts a fresh `caverns_and_claudes` / `caverns_sunden`
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
- `caverns_and_claudes/worlds/caverns_sunden/` had NO `magic.yaml`
  file at all — the immediate bug.

Fix (content-only):
- Migrated `caverns_and_claudes/magic.yaml` to the canonical schema
  (matching `space_opera/magic.yaml` + `tests/magic/fixtures/
  space_opera_magic.yaml`). Original draft notes preserved as
  comments for future authoring reference.
- Created `caverns_and_claudes/worlds/caverns_sunden/magic.yaml`
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

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
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
    world_yaml = cc_pack_dir / "worlds" / "caverns_sunden" / "magic.yaml"
    assert genre_yaml.exists(), "C&C genre magic.yaml must exist"
    assert world_yaml.exists(), (
        "C&C caverns_sunden world magic.yaml must exist — "
        "init_magic_state_for_session requires both tiers"
    )
    # Should not raise.
    config = load_world_magic(genre_yaml=genre_yaml, world_yaml=world_yaml)
    assert config.world_slug == "caverns_sunden"
    assert config.genre_slug == "caverns_and_claudes"


def test_caverns_and_claudes_caverns_sunden_magic_init_fires(cc_pack_dir):
    """End-to-end: `init_magic_state_for_session` returns True for
    Mira's session shape and populates `snapshot.magic_state` with
    BOTH plugins active post-2026-05-07 B/X pivot (item_legacy_v1 +
    innate_v1).
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    result = init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="caverns_sunden",
        character_id="Mira",
        character_class="Delver",
    )
    assert result is True, (
        "init_magic_state_for_session must return True for "
        "caverns_and_claudes/caverns_sunden — pre-fix returned "
        "False because the world yaml was missing"
    )
    assert snapshot.magic_state is not None
    assert snapshot.magic_state.config.world_slug == "caverns_sunden"
    assert snapshot.magic_state.config.genre_slug == "caverns_and_claudes"
    # Post-pivot: BOTH plugins are permitted/active for caverns_sunden.
    active = snapshot.magic_state.config.active_plugins
    assert "item_legacy_v1" in active
    assert "innate_v1" in active


def test_caverns_sunden_ships_one_character_scope_spell_slots_bar(cc_pack_dir):
    """Post-2026-05-07 B/X pivot: caverns_sunden ships one character-
    scope ``spell_slots`` bar with class-keyed ``starts_at_chargen``.
    A Delver-class chargen lands at 0.0 (non-caster); the bar exists
    so that a future Mage commit can see the same registry.
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="caverns_sunden",
        character_id="Mira",
        character_class="Delver",
    )
    bars = list(snapshot.magic_state.ledger.values())
    assert len(bars) == 1, (
        f"caverns_sunden should ship exactly one character-scope bar "
        f"(spell_slots) seeded by add_character; got {len(bars)} bars"
    )
    assert bars[0].spec.id == "spell_slots"
    assert bars[0].value == 0.0, (
        "Delver is a non-caster; spell_slots must seed at 0.0 per "
        "world magic.yaml class-keyed starts_at_chargen"
    )


def test_caverns_sunden_class_aware_spell_slot_allocation(cc_pack_dir):
    """B/X canon at L1: Magic-User gets 1 slot/day; Cleric gets 0
    (casting begins L2); Fighter and Thief get 0 (non-casters);
    Delver gets 0 (pre-class default). The class-keyed
    ``starts_at_chargen`` dict in caverns_sunden/magic.yaml encodes
    these values; ``add_character`` resolves them by
    ``character.char_class`` (display-cased).
    """
    expected = {
        "Mage": 1.0,
        "Cleric": 0.0,
        "Fighter": 0.0,
        "Thief": 0.0,
        "Delver": 0.0,
    }
    for char_class, expected_value in expected.items():
        snapshot = GameSnapshot(
            genre_slug="caverns_and_claudes",
            world_slug="caverns_sunden",
            turn_manager=TurnManager(),
        )
        init_magic_state_for_session(
            snapshot=snapshot,
            genre_pack_source_dir=cc_pack_dir,
            world_slug="caverns_sunden",
            character_id=f"Test_{char_class}",
            character_class=char_class,
        )
        bars = list(snapshot.magic_state.ledger.values())
        assert len(bars) == 1
        assert bars[0].value == expected_value, (
            f"{char_class!r} expected spell_slots={expected_value}, "
            f"got {bars[0].value}"
        )


def test_caverns_sunden_mage_cast_routes_to_spell_slots_bar(cc_pack_dir):
    """Playtest 2026-05-08 regression: a magic_working with cost_type
    `spell_slots` MUST debit the `spell_slots` ledger bar end-to-end.

    Pre-fix, world cost_types_active listed `slots` while the bar id
    was `spell_slots`; the engine routes by ``BarKey(scope, owner,
    bar_id=cost_type)`` so a `slots` cost found no matching bar, fell
    through to the world/item-scope-pending guard, and emitted a
    `magic.unrouted_cost` warning. Every Mage cast was a no-op against
    the ledger — the textbook SOUL.md illusionism failure.

    The fix aligns cost_type with bar id (`spell_slots` everywhere).
    This test asserts that alignment by exercising the routing path
    end-to-end against the shipped content (no synthetic fixture).
    """
    from sidequest.magic.models import MagicWorking

    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="caverns_sunden",
        character_id="Ponder",
        character_class="Mage",
    )

    # Pre-cast: B/X L1 Mage starts with 1 prepared spell slot.
    spell_slots_key = next(iter(snapshot.magic_state.ledger))
    assert snapshot.magic_state.ledger[spell_slots_key].value == 1.0

    working = MagicWorking(
        plugin="innate_v1",
        mechanism="native",
        actor="Ponder",
        costs={"spell_slots": 1.0},
        domain="elemental",
        narrator_basis="Mage casts the prepared Light spell",
        flavor="acquired",
        consent_state="willing",
    )
    result = snapshot.magic_state.apply_working(working)

    # Post-cast: the slot is gone, the bar dropped 1.0 → 0.0, and the
    # routing actually engaged (bar_changes is non-empty — a routed
    # cost; an unrouted cost would skip the assignment and leave
    # bar_changes empty with a `magic.unrouted_cost` warning).
    assert "spell_slots" in result.bar_changes, (
        "spell_slots cost must route to the spell_slots bar — empty "
        "bar_changes means the cost dropped silently (the playtest bug)"
    )
    prev, post = result.bar_changes["spell_slots"]
    assert prev == 1.0
    assert post == 0.0


def test_caverns_sunden_cost_types_match_character_scope_bar_ids(cc_pack_dir):
    """Engine convention: ``MagicState.apply_working`` looks up bars by
    ``BarKey(scope, owner, bar_id=cost_type)``. For every character-scope
    bar a world ships, the bar's id MUST appear in cost_types — else a
    narrator-emitted cost against that bar will drop silently.

    Authored cost_types may legitimately exceed character bar ids when
    the cost targets world/item scope (engine routing for those scopes
    is pending; ``magic.state.apply_working`` logs `magic.unrouted_cost`
    for them). The reverse direction is the bug: an authored character
    bar with no matching cost_type means there's no narrator-side path
    to reach it. This test pins that direction.
    """
    config = load_world_magic(
        genre_yaml=cc_pack_dir / "magic.yaml",
        world_yaml=cc_pack_dir / "worlds" / "caverns_sunden" / "magic.yaml",
    )
    character_bar_ids = {b.id for b in config.ledger_bars if b.scope == "character"}
    cost_type_set = set(config.cost_types)
    missing = character_bar_ids - cost_type_set
    assert not missing, (
        f"caverns_sunden ships character-scope bar(s) {sorted(missing)} "
        f"with no matching cost_type; narrator-emitted costs cannot reach "
        f"them. Add to cost_types_active in worlds/caverns_sunden/magic.yaml."
    )


def test_caverns_sunden_unknown_class_raises_loudly(cc_pack_dir):
    """No silent fallback: if a character is committed with a class
    that isn't in the world's ``starts_at_chargen`` dict, ``add_character``
    raises ValueError. Production callers (websocket_session_handler /
    connect handler) cannot reach this state because Character validates
    char_class at build time, but the schema enforcement here catches
    YAML/class-list drift early.
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    with pytest.raises(ValueError, match=r"missing from starts_at_chargen"):
        init_magic_state_for_session(
            snapshot=snapshot,
            genre_pack_source_dir=cc_pack_dir,
            world_slug="caverns_sunden",
            character_id="Test_Bard",
            character_class="Bard",  # not in classes.yaml; not in dict
        )


def test_caverns_sunden_missing_class_param_raises_loudly(cc_pack_dir):
    """Class-keyed ``starts_at_chargen`` requires the caller to pass
    ``character_class``. Omitting it raises ValueError — defends against
    a future caller forgetting to thread the class through (the bug this
    whole change exists to fix).
    """
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    with pytest.raises(ValueError, match=r"no character_class was supplied"):
        init_magic_state_for_session(
            snapshot=snapshot,
            genre_pack_source_dir=cc_pack_dir,
            world_slug="caverns_sunden",
            character_id="Mira",
            # character_class intentionally omitted
        )


def test_caverns_and_claudes_intensity_and_world_knowledge(cc_pack_dir):
    """Smoke: the migration preserved C&C's authored values
    (folkloric world-knowledge, hard limits) — guards against
    accidental shape drift if the yaml is touched again. Note:
    intensity moved from 0.3 (genre default) to 0.4 (world layer)
    in the 2026-05-07 B/X pivot to reflect the addition of the
    caster surface.
    """
    config = load_world_magic(
        genre_yaml=cc_pack_dir / "magic.yaml",
        world_yaml=cc_pack_dir / "worlds" / "caverns_sunden" / "magic.yaml",
    )
    assert config.intensity == 0.4
    assert config.world_knowledge.primary == "folkloric"
    assert "components" in config.cost_types
    assert "backlash" in config.cost_types
    # B/X spell-slot debits route by `cost_type == bar_id` (engine convention
    # at magic/state.py); cost_type name must match the `spell_slots` ledger
    # bar id or every cast drops silently (playtest 2026-05-08).
    assert "spell_slots" in config.cost_types
    # The pre-fix cost_type name `slots` is gone — keep the regression pinned.
    assert "slots" not in config.cost_types
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
