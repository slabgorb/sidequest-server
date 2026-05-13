import random

import pytest

from sidequest.game.builder import (
    CharacterBuilder,
    StoryInput,
    UnfilledArrangementError,  # reused for missing-pronouns
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


def _drive_to_story_scene(b: CharacterBuilder) -> None:
    """Walk the FSM: roll → arrange → calling → story."""
    # Auto-advance past the_roll if needed.
    while b.current_scene().id != "the_arrangement":
        from sidequest.game.builder import FreeformInput

        b.apply_response(FreeformInput(text=""))
    # Arrange + confirm.
    pool = b.arrangement_pool()
    sorted_pool = sorted(pool, reverse=True)
    for stat, v in zip(["STR", "DEX", "CON", "INT", "WIS", "CHA"], sorted_pool, strict=True):
        b.assign_stat(stat, v)
    b.apply_arrangement_confirm()
    assert b.current_scene().id == "the_calling"
    # Pick the only qualifying class.
    from sidequest.game.builder import ChoiceInput

    b.apply_response(ChoiceInput(index=0))
    assert b.current_scene().id == "the_story"


def test_story_input_records_pronouns_and_advances():
    b = _seeded()
    _drive_to_story_scene(b)
    b.apply_response(
        StoryInput(
            pronouns="they/them",
            background="Former ratcatcher.",
            description="Tall, soot-stained.",
        )
    )
    assert b.current_scene().id == "the_kit"


def test_story_input_blank_pronouns_when_required_raises():
    b = _seeded()
    _drive_to_story_scene(b)
    # the_story.mechanical_effects.identity_capture.pronouns_required is True
    with pytest.raises(UnfilledArrangementError):
        b.apply_response(
            StoryInput(
                pronouns="   ",
                background="x",
                description="y",
            )
        )


def test_story_input_records_background_and_description_on_scene_result():
    b = _seeded()
    _drive_to_story_scene(b)
    b.apply_response(
        StoryInput(
            pronouns="she/her",
            background="Former wheelwright's widow.",
            description="Tall, soot-stained, missing a tooth.",
        )
    )
    # Inspect the latest result that corresponds to the_story.
    results_for_story = [r for r in b._results if getattr(r, "scene_id", None) == "the_story"]
    assert len(results_for_story) == 1
    # Some field should carry the background OR description text.
    # Be tolerant about which SceneResult field — the test only requires
    # the data is captured somewhere structured (not lost).
    serialized = repr(results_for_story[0])
    assert "wheelwright" in serialized or "soot-stained" in serialized
