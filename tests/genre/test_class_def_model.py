import pytest
from pydantic import ValidationError

from sidequest.genre.models.character import ClassDef, EquipmentTables


def test_classdef_minimal_valid():
    c = ClassDef(
        id="fighter",
        display_name="Fighter",
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite="STR",
        minimum_score=9,
        kit_table="fighter_kit",
    )
    assert c.id == "fighter"
    assert c.encounter_beat_choices == []
    assert c.magic_access is None


def test_classdef_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ClassDef(
            id="fighter",
            display_name="Fighter",
            rpg_role="tank",
            jungian_default="hero",
            prime_requisite="STR",
            minimum_score=9,
            kit_table="fighter_kit",
            unknown_field="boom",
        )


def test_classdef_full_optional_fields():
    c = ClassDef(
        id="mage",
        display_name="Mage",
        rpg_role="control",
        jungian_default="magician",
        prime_requisite="INT",
        minimum_score=9,
        kit_table="mage_kit",
        flavor="A bookish nuisance.",
        encounter_beat_choices=[],
        magic_access=None,
    )
    assert c.flavor == "A bookish nuisance."


def test_equipment_tables_class_tables_default_empty():
    et = EquipmentTables()
    assert et.class_tables == {}


def test_equipment_tables_class_tables_loads_nested():
    et = EquipmentTables.model_validate(
        {
            "tables": {"weapon": ["dagger"]},
            "class_tables": {
                "fighter_kit": {"weapon": ["sword_long"]},
                "mage_kit": {"weapon": ["staff_wood"]},
            },
        }
    )
    assert "fighter_kit" in et.class_tables
    assert et.class_tables["mage_kit"]["weapon"] == ["staff_wood"]
