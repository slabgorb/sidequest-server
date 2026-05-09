"""Unit tests for qualifying_classes_arrangement() — None-tolerant variant."""

from sidequest.game.builder import qualifying_classes_arrangement
from sidequest.genre.models.character import ClassDef


def _cls(name: str, prime: str, minimum: int) -> ClassDef:
    """Minimal ClassDef per sidequest/genre/models/character.py:95."""
    return ClassDef(
        id=name.lower(),
        display_name=name,
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite=prime,
        minimum_score=minimum,
        kit_table=f"{name.lower()}_kit",
    )


def test_arrangement_qualifies_when_any_stat_meets_threshold():
    fighter = _cls("Fighter", "STR", 9)
    mage = _cls("Mage", "INT", 9)
    arrangement = {"STR": 14, "DEX": 8, "CON": 10, "INT": 6, "WIS": 7, "CHA": 11}
    result = qualifying_classes_arrangement(arrangement, [fighter, mage])
    assert [c.display_name for c in result] == ["Fighter"]


def test_arrangement_qualifies_multiple_classes():
    fighter = _cls("Fighter", "STR", 9)
    thief = _cls("Thief", "DEX", 9)
    arrangement = {"STR": 14, "DEX": 12, "CON": 10, "INT": 6, "WIS": 7, "CHA": 11}
    result = qualifying_classes_arrangement(arrangement, [fighter, thief])
    assert {c.display_name for c in result} == {"Fighter", "Thief"}


def test_arrangement_qualifies_none_when_all_low():
    fighter = _cls("Fighter", "STR", 9)
    mage = _cls("Mage", "INT", 9)
    arrangement = {"STR": 8, "DEX": 8, "CON": 8, "INT": 8, "WIS": 8, "CHA": 8}
    result = qualifying_classes_arrangement(arrangement, [fighter, mage])
    assert result == []


def test_arrangement_partial_unfilled_treated_as_zero():
    fighter = _cls("Fighter", "STR", 9)
    arrangement = {"STR": 14, "DEX": None, "CON": None, "INT": None, "WIS": None, "CHA": None}
    result = qualifying_classes_arrangement(arrangement, [fighter])
    assert [c.display_name for c in result] == ["Fighter"]
