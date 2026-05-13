"""Story 2026-05-10 — protocol contract for full AbilityDefinition + class_moves."""

from __future__ import annotations

from sidequest.game.ability import AbilitySource
from sidequest.game.character import AbilityDefinition
from sidequest.protocol.models import CharacterSheetDetails


def _ab(name: str, source: AbilitySource = AbilitySource.Class) -> AbilityDefinition:
    return AbilityDefinition(
        name=name,
        genre_description=f"{name} prose.",
        mechanical_effect=f"{name} effect.",
        involuntary=False,
        source=source,
    )


def test_abilities_serializes_as_full_objects_not_strings():
    sheet = CharacterSheetDetails(
        race="Human",
        stats={"STR": 10},
        abilities=[_ab("Turn Undead")],
        backstory="Backstory",
        personality="Devout",
        equipment=[],
        class_moves=["pray", "shield_bash", "turn_undead"],
    )
    dumped = sheet.model_dump()
    assert isinstance(dumped["abilities"], list)
    assert dumped["abilities"][0]["name"] == "Turn Undead"
    assert dumped["abilities"][0]["source"] == "Class"
    assert dumped["abilities"][0]["genre_description"] == "Turn Undead prose."


def test_class_moves_field_exists_and_is_list_of_str():
    sheet = CharacterSheetDetails(
        race="Human",
        stats={},
        abilities=[],
        backstory="x",
        personality="y",
        equipment=[],
        class_moves=["pray", "shield_bash"],
    )
    assert sheet.class_moves == ["pray", "shield_bash"]


def test_views_build_filters_universal_beats_and_autofilled():
    """The view layer drops attack/defend/flee + 'auto-filled' before sending to UI."""
    from sidequest.server.views import _filter_class_moves

    raw = ["attack", "defend", "flee", "shield_bash", "turn_undead", "pray", "thing-auto-filled"]
    filtered = _filter_class_moves(raw)
    assert filtered == ["shield_bash", "turn_undead", "pray"]
