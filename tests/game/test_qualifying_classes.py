"""Unit tests for qualifying_classes() — prime-requisite filter."""

from sidequest.game.builder import qualifying_classes
from sidequest.genre.models.character import ClassDef


def _class(id_: str, prime: str, minimum: int = 9) -> ClassDef:
    return ClassDef(
        id=id_,
        display_name=id_.capitalize(),
        rpg_role="tank",
        jungian_default="hero",
        prime_requisite=prime,
        minimum_score=minimum,
        kit_table=f"{id_}_kit",
    )


CLASSES = [
    _class("fighter", "STR"),
    _class("mage", "INT"),
    _class("cleric", "WIS"),
    _class("thief", "DEX"),
]


def test_all_low_returns_empty():
    stats = {"STR": 8, "DEX": 8, "CON": 8, "INT": 8, "WIS": 8, "CHA": 8}
    assert qualifying_classes(stats, CLASSES) == []


def test_strong_str_only_returns_fighter():
    stats = {"STR": 14, "DEX": 8, "CON": 8, "INT": 8, "WIS": 8, "CHA": 8}
    result = [c.id for c in qualifying_classes(stats, CLASSES)]
    assert result == ["fighter"]


def test_strong_everything_returns_all():
    stats = {"STR": 14, "DEX": 14, "CON": 14, "INT": 14, "WIS": 14, "CHA": 14}
    result = sorted(c.id for c in qualifying_classes(stats, CLASSES))
    assert result == ["cleric", "fighter", "mage", "thief"]


def test_boundary_exact_minimum_qualifies():
    stats = {"STR": 9, "DEX": 8, "CON": 8, "INT": 8, "WIS": 8, "CHA": 8}
    assert [c.id for c in qualifying_classes(stats, CLASSES)] == ["fighter"]


def test_boundary_below_minimum_does_not_qualify():
    stats = {"STR": 8, "DEX": 8, "CON": 8, "INT": 8, "WIS": 8, "CHA": 8}
    assert qualifying_classes(stats, CLASSES) == []


def test_missing_stat_does_not_qualify():
    stats = {"STR": 14}
    result = [c.id for c in qualifying_classes(stats, CLASSES)]
    assert result == ["fighter"]


def test_empty_class_list_returns_empty():
    stats = {"STR": 14, "DEX": 14, "CON": 14, "INT": 14, "WIS": 14, "CHA": 14}
    assert qualifying_classes(stats, []) == []
