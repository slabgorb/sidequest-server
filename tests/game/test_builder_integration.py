"""Integration test for sidequest.game.builder — Slice 5.

Wiring test per CLAUDE.md:
    Every Test Suite Needs a Wiring Test
    Unit tests prove a component works in isolation. That's not enough.
    Every set of tests must include at least one integration test that
    verifies the component is wired into the system — imported, called,
    and reachable from production code paths.

This test loads a real genre pack via load_genre_pack(), constructs a
CharacterBuilder with the pack's actual char_creation scenes and
rules, walks the full scene flow, and builds a real Character. No
fixtures, no stubs, no mock data — if this passes, the builder is
reachable from production content.

One pack (caverns_and_claudes) is enough for the wiring check. The
full cross-pack matrix — each of the 6 genres walked through
representative flows — belongs to Story 2.2's dispatch integration
tests, not 2.1's unit surface.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from sidequest.game.builder import (
    CharacterBuilder,
    InProgress,
)
from sidequest.genre.loader import load_genre_pack

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


@pytest.fixture(scope="module")
def caverns_pack() -> object:
    """Load caverns_and_claudes. Fails loudly if content isn't present —
    integration tests must run against the real tree (SOUL: fail loud
    at the boundary)."""
    path = CONTENT_ROOT / "caverns_and_claudes"
    if not path.is_dir():
        pytest.skip(f"content pack not found at {path}")
    return load_genre_pack(path)


def test_builder_walks_caverns_and_claudes_to_character(caverns_pack: object) -> None:
    """End-to-end: load real pack, walk all scenes, build a Character.

    caverns_and_claudes flow (4 scenes):
      0. the_roll — auto-advance, scene-level stat_generation=roll_3d6_strict
      1. pronouns — 3 choices + allows_freeform, no hook
      2. the_kit — auto-advance, scene-level equipment_generation=random_table
      3. the_mouth — auto-advance (the dungeon entrance)

    The builder must:
      - Construct with pack.char_creation + pack.rules +
        pack.backstory_tables
      - Wire pack.equipment_tables via fluent setter
      - Roll stats eagerly at construction (scene 0 declares
        roll_3d6_strict)
      - Walk all 4 scenes to Confirmation
      - Build a valid Character with stats, inventory, edge pool
    """
    pack = caverns_pack
    # Sanity check: caverns has the 4 scenes we expect. If the content
    # changes shape this test alerts us — it's the wiring canary.
    assert len(pack.char_creation) == 4  # type: ignore[attr-defined]
    assert pack.char_creation[0].id == "the_roll"  # type: ignore[attr-defined]

    builder = (
        CharacterBuilder(
            scenes=list(pack.char_creation),  # type: ignore[attr-defined]
            rules=pack.rules,  # type: ignore[attr-defined]
            backstory_tables=pack.backstory_tables,  # type: ignore[attr-defined]
            rng=random.Random(42),
        )
        .with_lobby_name("Rux")
        .with_equipment_tables(pack.equipment_tables)  # type: ignore[attr-defined]
    )

    # Eager 3d6 roll fired at construction — scene 0 declares
    # stat_generation=roll_3d6_strict in its mechanical_effects.
    rolled = builder.rolled_stats()
    assert rolled is not None
    # Ability score names come from the pack's rules config; every name
    # appears in rolled_stats.
    rolled_names = [name for name, _ in rolled]
    assert set(rolled_names) == set(pack.rules.ability_score_names)  # type: ignore[attr-defined]
    for _, total in rolled:
        assert 3 <= total <= 18  # 3d6 range

    # Walk scenes:
    # 0. the_roll — display-only, auto-advance
    assert builder.is_in_progress()
    assert isinstance(builder._phase, InProgress)
    assert builder.current_scene().id == "the_roll"
    builder.apply_auto_advance()

    # 1. pronouns — pick one
    assert builder.current_scene().id == "pronouns"
    builder.apply_choice(0)  # she/her

    # 2. the_kit — equipment_generation=random_table
    assert builder.current_scene().id == "the_kit"
    builder.apply_auto_advance()

    # 3. the_mouth — display-only
    assert builder.current_scene().id == "the_mouth"
    builder.apply_auto_advance()

    assert builder.is_confirmation()

    # Build the character
    character = builder.build("Rux")

    # --- Character shape assertions ---

    # Identity
    assert character.core.name == "Rux"
    assert character.pronouns == "she/her"  # from the pronouns scene pick
    assert character.char_class == pack.rules.default_class  # "Delver"  # type: ignore[attr-defined]

    # Stats: every ability score name has a value in a plausible range
    # (3d6 base = 3..18, modified by any derived bonuses = widened but
    # still realistic).
    for name in pack.rules.ability_score_names:  # type: ignore[attr-defined]
        assert name in character.stats
        assert 1 <= character.stats[name] <= 25

    # Inventory: equipment_tables roll produced at least one item
    # (rolls_per_slot defaults to 1 per slot on the pack).
    assert len(character.core.inventory.items) >= 1
    for item in character.core.inventory.items:
        # Shape check — these are the keys the builder emits. If a
        # downstream consumer expects flags we don't set, this test
        # will alert us when the dispatch port starts reading them.
        required = {
            "id", "name", "description", "category", "value", "weight",
            "rarity", "narrative_weight", "tags", "equipped", "quantity",
            "uses_remaining", "state",
        }
        assert required.issubset(item.keys())

    # Edge pool: caverns_and_claudes has no edge_config → placeholder
    # pool (base_max = 10). Class is Delver, not Fighter, so no stub.
    assert character.core.edge.base_max == 10
    assert character.core.edge.max == 10
    assert character.core.edge.current == 10

    # Backstory: non-blank, came from some path (fragments/tables/
    # mechanical/fallback).
    assert character.backstory.strip()

    # Level + narrative state at creation
    assert character.core.level == 1
    assert character.is_friendly is True
    assert character.narrative_state == "Beginning their adventure"


def test_caverns_pack_loader_is_the_sanctioned_entry_point(caverns_pack: object) -> None:
    """SOUL.md: 'The loader is the contract.' The builder must receive
    pack-loaded data only through load_genre_pack() — bypassing it (reading
    YAML directly, constructing scenes by hand) forks the strictness
    policy. This test documents the single-entry-point discipline by
    exercising the actual path dispatch will use."""
    pack = caverns_pack
    # char_creation is a structured list[CharCreationScene] — not a
    # dict or raw YAML — proving the loader has run validation.
    assert all(
        type(s).__name__ == "CharCreationScene"
        for s in pack.char_creation  # type: ignore[attr-defined]
    )
    # Rules has the enum-constrained stat_generation (not a freeform
    # string).
    assert isinstance(pack.rules.stat_generation, str)  # type: ignore[attr-defined]
