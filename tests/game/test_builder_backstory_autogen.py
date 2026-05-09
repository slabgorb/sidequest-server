"""Tests for CharacterBuilder.autogen_backstory — deterministic table roll."""

import random

from sidequest.game.builder import CharacterBuilder
from sidequest.genre.models.character import BackstoryTables
from sidequest.genre.models.rules import RulesConfig
from tests.game.test_builder_arrange_visible import _make_scenes_with_arrange_visible


def _tables() -> BackstoryTables:
    """C&C-shape backstory tables: template + slot lists.

    Built via model_validate so the dynamic-table extraction matches
    how genre-pack YAML loads in production.
    """
    return BackstoryTables.model_validate(
        {
            "template": "Former {trade}. {feature}. {reason}.",
            "trade": ["ratcatcher", "tinker", "ditch digger"],
            "feature": [
                "one glass eye",
                "missing three fingers",
                "no eyebrows",
            ],
            "reason": ["debt collectors", "a curse", "a bad pig butcher"],
        }
    )


def _builder_with_tables() -> CharacterBuilder:
    rules = RulesConfig(
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
        stat_generation="roll_3d6_arrange_visible",
    )
    return CharacterBuilder(
        scenes=_make_scenes_with_arrange_visible(),
        rules=rules,
        rng=random.Random(0),
        backstory_tables=_tables(),
    )


def test_autogen_returns_background_and_description_keys():
    b = _builder_with_tables()
    out = b.autogen_backstory(seed=42)
    assert set(out.keys()) == {"background", "description"}
    assert isinstance(out["background"], str)
    assert isinstance(out["description"], str)


def test_autogen_background_is_non_empty():
    b = _builder_with_tables()
    out = b.autogen_backstory(seed=42)
    assert out["background"]


def test_autogen_deterministic_with_same_seed():
    b1 = _builder_with_tables()
    b2 = _builder_with_tables()
    assert b1.autogen_backstory(seed=7) == b2.autogen_backstory(seed=7)


def test_autogen_varies_with_different_seed():
    b = _builder_with_tables()
    a = b.autogen_backstory(seed=1)
    z = b.autogen_backstory(seed=999)
    assert a != z


def test_autogen_no_tables_returns_empty_strings():
    rules = RulesConfig(
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
        stat_generation="roll_3d6_arrange_visible",
    )
    b = CharacterBuilder(
        scenes=_make_scenes_with_arrange_visible(),
        rules=rules,
        rng=random.Random(0),
    )
    out = b.autogen_backstory(seed=42)
    assert out == {"background": "", "description": ""}
