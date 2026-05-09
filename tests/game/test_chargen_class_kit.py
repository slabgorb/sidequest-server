"""class_kit equipment generation dispatch test.

Verifies that equipment_generation=class_kit rolls from the chosen class's
kit_table rather than the generic random_table.
"""

from __future__ import annotations

import random

from sidequest.game.builder import CharacterBuilder
from sidequest.genre.models.character import (
    CharCreationChoice,
    CharCreationScene,
    ClassDef,
    EquipmentTables,
    MechanicalEffects,
)
from sidequest.genre.models.rules import RulesConfig


def _make_rules() -> RulesConfig:
    return RulesConfig(
        stat_generation="standard_array",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )


def _make_classes() -> list[ClassDef]:
    return [
        ClassDef(
            id="fighter",
            display_name="Fighter",
            rpg_role="tank",
            jungian_default="hero",
            prime_requisite="STR",
            minimum_score=9,
            kit_table="fighter_kit",
        ),
        ClassDef(
            id="mage",
            display_name="Mage",
            rpg_role="dps",
            jungian_default="sage",
            prime_requisite="INT",
            minimum_score=9,
            kit_table="mage_kit",
        ),
    ]


def _make_equipment_tables() -> EquipmentTables:
    return EquipmentTables(
        tables={
            "weapon": ["sword_long", "mace_iron"],
            "armor": ["leather_armor"],
        },
        rolls_per_slot={"weapon": 1, "armor": 1},
        class_tables={
            "fighter_kit": {
                "weapon": ["sword_long"],
                "armor": ["plate_mail"],
            },
            "mage_kit": {
                "weapon": ["staff_wood"],
                "armor": [],
                "utility": ["spellbook"],
            },
        },
    )


def _make_class_kit_scenes(class_hint: str) -> list[CharCreationScene]:
    """Two-scene chargen: choose class, then roll class_kit."""
    return [
        CharCreationScene(
            id="class_choice",
            title="Choose Your Path",
            narration="What are you?",
            choices=[
                CharCreationChoice(
                    label="Fighter",
                    description="A warrior.",
                    mechanical_effects=MechanicalEffects(class_hint="Fighter"),
                ),
                CharCreationChoice(
                    label="Mage",
                    description="A wizard.",
                    mechanical_effects=MechanicalEffects(class_hint="Mage"),
                ),
            ],
        ),
        CharCreationScene(
            id="the_kit",
            title="Your Equipment",
            narration="Here is your gear.",
            mechanical_effects=MechanicalEffects(equipment_generation="class_kit"),
        ),
    ]


def test_class_kit_only_rolls_from_chosen_class_kit_fighter():
    """Force class_hint=Fighter; verify items all come from fighter_kit."""
    scenes = _make_class_kit_scenes("Fighter")
    rules = _make_rules()
    tables = _make_equipment_tables()
    classes = _make_classes()

    builder = (
        CharacterBuilder(scenes, rules, rng=random.Random(42))
        .with_equipment_tables(tables)
        .with_classes(classes)
    )

    # Scene 0: choose Fighter (index 0)
    builder.apply_choice(0)
    # Scene 1: auto-advance (class_kit scene)
    builder.apply_auto_advance()

    assert builder.is_confirmation()
    character = builder.build("TestFighter")

    item_ids = [item["id"] for item in character.core.inventory.items]
    # fighter_kit weapon = sword_long only; armor = plate_mail only.
    # No mage items (staff_wood, spellbook) should appear.
    assert "staff_wood" not in item_ids
    assert "spellbook" not in item_ids
    # At least one fighter-kit item should be present.
    fighter_kit_ids = {"sword_long", "plate_mail"}
    assert any(iid in fighter_kit_ids for iid in item_ids), (
        f"Expected fighter_kit items in inventory, got: {item_ids}"
    )


def test_class_kit_only_rolls_from_chosen_class_kit_mage():
    """Force class_hint=Mage; verify items all come from mage_kit."""
    scenes = _make_class_kit_scenes("Mage")
    rules = _make_rules()
    tables = _make_equipment_tables()
    classes = _make_classes()

    builder = (
        CharacterBuilder(scenes, rules, rng=random.Random(42))
        .with_equipment_tables(tables)
        .with_classes(classes)
    )

    # Scene 0: choose Mage (index 1)
    builder.apply_choice(1)
    # Scene 1: auto-advance (class_kit scene)
    builder.apply_auto_advance()

    assert builder.is_confirmation()
    character = builder.build("TestMage")

    item_ids = [item["id"] for item in character.core.inventory.items]
    # mage_kit weapon = staff_wood only; no plate_mail or sword_long.
    assert "plate_mail" not in item_ids
    assert "sword_long" not in item_ids
    # At least staff_wood or spellbook should appear.
    mage_kit_ids = {"staff_wood", "spellbook"}
    assert any(iid in mage_kit_ids for iid in item_ids), (
        f"Expected mage_kit items in inventory, got: {item_ids}"
    )


def test_random_table_still_works():
    """Verify random_table behavior is unbroken for packs that use it."""
    scenes = [
        CharCreationScene(
            id="the_kit",
            title="Kit",
            narration="Your gear.",
            mechanical_effects=MechanicalEffects(equipment_generation="random_table"),
        ),
    ]
    rules = _make_rules()
    tables = _make_equipment_tables()

    builder = CharacterBuilder(scenes, rules, rng=random.Random(42)).with_equipment_tables(tables)
    builder.apply_auto_advance()
    assert builder.is_confirmation()
    character = builder.build("TestRandom")

    item_ids = [item["id"] for item in character.core.inventory.items]
    # random_table draws from tables (sword_long/mace_iron, leather_armor).
    valid_ids = {"sword_long", "mace_iron", "leather_armor"}
    assert all(iid in valid_ids for iid in item_ids), (
        f"Expected only random_table items, got: {item_ids}"
    )
