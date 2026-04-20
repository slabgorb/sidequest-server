"""Tests for ``sidequest.server.dispatch.chargen_loadout.apply_starting_loadout``.

Covers:
- Class-specific equipment is appended, gold is added.
- Case-insensitive class lookup (pack uses "Delver", character has "delver").
- Items not in ``item_catalog`` fall through to the minimal branch — still
  honored, never silently dropped.
- Pack with no ``inventory`` config is a no-op.
- Character class that isn't in ``starting_equipment`` is a no-op (no
  items added, no gold added) — Rust parity (find returns None).
- Builder-side item_hints already on inventory.items are preserved;
  loadout appends rather than replaces.
"""

from __future__ import annotations

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, EdgePool, RecoveryTrigger
from sidequest.genre.models.inventory import (
    CatalogItem,
    InventoryConfig,
)
from sidequest.server.dispatch.chargen_loadout import apply_starting_loadout


def _make_character(char_class: str = "Delver") -> Character:
    edge = EdgePool(
        current=20,
        max=20,
        base_max=20,
        recovery_triggers=[RecoveryTrigger.OnResolution],
        thresholds=[],
    )
    core = CreatureCore(
        name="Rux",
        description="A seasoned delver",
        personality="Curious",
        level=1,
        xp=0,
        edge=edge,
    )
    return Character(
        core=core,
        backstory="An orphan of the Reach.",
        char_class=char_class,
        race="Gnome",
    )


def _basic_catalog() -> list[CatalogItem]:
    return [
        CatalogItem(
            id="rusted_lantern",
            name="Rusted Lantern",
            description="Throws a weak amber glow.",
            category="tool",
            value=3,
            weight=1.5,
            rarity="common",
            tags=["light"],
        ),
        CatalogItem(
            id="short_rope",
            name="Short Rope",
            description="Ten feet of hemp.",
            category="tool",
            value=1,
            weight=2.0,
            rarity="",  # blank rarity → loadout fills in "common"
            tags=["climbing"],
            resource_ticks=3,
        ),
    ]


def test_class_equipment_and_gold_appended() -> None:
    char = _make_character("Delver")
    config = InventoryConfig(
        item_catalog=_basic_catalog(),
        starting_equipment={"Delver": ["rusted_lantern", "short_rope"]},
        starting_gold={"Delver": 7},
    )

    items_added, gold_added = apply_starting_loadout(char, config)

    assert items_added == 2
    assert gold_added == 7
    assert char.core.inventory.gold == 7
    assert [i["id"] for i in char.core.inventory.items] == [
        "rusted_lantern",
        "short_rope",
    ]
    lantern = char.core.inventory.items[0]
    assert lantern["name"] == "Rusted Lantern"
    assert lantern["narrative_weight"] == 0.3
    assert lantern["equipped"] is False
    assert lantern["quantity"] == 1
    assert lantern["state"] == "Carried"

    rope = char.core.inventory.items[1]
    assert rope["rarity"] == "common", "blank catalog rarity must default to 'common'"
    assert rope["uses_remaining"] == 3


def test_class_match_is_case_insensitive() -> None:
    char = _make_character("delver")  # character stores class lowercased
    config = InventoryConfig(
        item_catalog=_basic_catalog(),
        starting_equipment={"Delver": ["rusted_lantern"]},
        starting_gold={"Delver": 5},
    )

    items_added, gold_added = apply_starting_loadout(char, config)

    assert items_added == 1
    assert gold_added == 5


def test_item_not_in_catalog_uses_minimal_fallback() -> None:
    char = _make_character("Delver")
    config = InventoryConfig(
        item_catalog=_basic_catalog(),
        # mystery_token is NOT in the catalog — must still appear as an
        # inventory entry, never silently dropped.
        starting_equipment={"Delver": ["mystery_token"]},
        starting_gold={"Delver": 0},
    )

    items_added, _ = apply_starting_loadout(char, config)

    assert items_added == 1
    entry = char.core.inventory.items[0]
    assert entry["id"] == "mystery_token"
    assert entry["name"] == "mystery token"  # underscores → spaces
    assert entry["description"] == "Starting equipment"
    assert entry["rarity"] == "common"
    assert entry["narrative_weight"] == 0.2
    assert entry["tags"] == []


def test_no_inventory_config_is_noop() -> None:
    char = _make_character("Delver")
    items_added, gold_added = apply_starting_loadout(char, None)

    assert items_added == 0
    assert gold_added == 0
    assert char.core.inventory.items == []
    assert char.core.inventory.gold == 0


def test_unknown_class_is_noop() -> None:
    char = _make_character("Philosopher")  # class not present in the pack
    config = InventoryConfig(
        item_catalog=_basic_catalog(),
        starting_equipment={"Delver": ["rusted_lantern"]},
        starting_gold={"Delver": 3},
    )

    items_added, gold_added = apply_starting_loadout(char, config)

    assert items_added == 0
    assert gold_added == 0
    assert char.core.inventory.items == []
    assert char.core.inventory.gold == 0


def test_builder_item_hints_are_preserved() -> None:
    char = _make_character("Delver")
    # Simulate a builder-side item_hint already on the inventory.
    char.core.inventory.items.append(
        {
            "id": "family_charm",
            "name": "Family Charm",
            "description": "Given by a grandmother long gone.",
            "category": "trinket",
            "value": 0,
            "weight": 0.1,
            "rarity": "common",
            "narrative_weight": 0.5,
            "tags": ["sentimental"],
            "equipped": False,
            "quantity": 1,
            "uses_remaining": None,
            "state": "Carried",
        }
    )
    config = InventoryConfig(
        item_catalog=_basic_catalog(),
        starting_equipment={"Delver": ["rusted_lantern"]},
        starting_gold={"Delver": 2},
    )

    apply_starting_loadout(char, config)

    ids = [i["id"] for i in char.core.inventory.items]
    assert ids == ["family_charm", "rusted_lantern"], (
        "loadout must append to existing items, not replace them"
    )
