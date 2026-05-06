import pytest
from pydantic import ValidationError

from sidequest.genre.models.character import ClassDef


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
