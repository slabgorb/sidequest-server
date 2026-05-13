import random

import pytest

from sidequest.game.builder import (
    ArrangementSceneActiveError,
    CharacterBuilder,
    ChoiceInput,
    FreeformInput,
)
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import RulesConfig
from tests.game.test_builder_arrange_visible import _make_scenes_with_arrange_visible


def _seeded():
    rules = RulesConfig(
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
        stat_generation="roll_3d6_arrange_visible",
    )
    return CharacterBuilder(
        scenes=_make_scenes_with_arrange_visible(),
        rules=rules,
        rng=random.Random(42),
    ).with_classes(
        [
            ClassDef(
                id="fighter",
                display_name="Fighter",
                rpg_role="tank",
                jungian_default="hero",
                prime_requisite="STR",
                minimum_score=9,
                kit_table="fighter_kit",
            ),
        ]
    )


def _advance_to_arrangement(builder: CharacterBuilder) -> None:
    """Drive the builder from the_roll into the_arrangement.

    the_roll has empty choices and allows_freeform=False; apply_response
    with a FreeformInput should pass the "no choices => freeform allowed"
    branch in apply_freeform and advance.
    """
    while builder.current_scene().id != "the_arrangement":
        builder.apply_response(FreeformInput(text=""))


def test_arrangement_scene_blocks_choice_input():
    b = _seeded()
    _advance_to_arrangement(b)
    assert b.current_scene().id == "the_arrangement"
    with pytest.raises(ArrangementSceneActiveError):
        b.apply_response(ChoiceInput(index=0))


def test_apply_arrangement_confirm_advances_to_calling():
    b = _seeded()
    _advance_to_arrangement(b)
    pool = b.arrangement_pool()
    sorted_pool = sorted(pool, reverse=True)
    for stat, v in zip(["STR", "DEX", "CON", "INT", "WIS", "CHA"], sorted_pool, strict=True):
        b.assign_stat(stat, v)
    b.apply_arrangement_confirm()
    assert b.current_scene().id == "the_calling"


def test_apply_arrangement_reject_resets_pool_and_stays_on_arrangement():
    b = _seeded()
    _advance_to_arrangement(b)
    pool_before = list(b.arrangement_pool())
    b.assign_stat("STR", pool_before[0])
    b.apply_arrangement_reject()
    assert b.current_scene().id == "the_arrangement"
    assert b.arrangement_pool() is not None
    assert all(v is None for v in b.arrangement_assignment().values())
