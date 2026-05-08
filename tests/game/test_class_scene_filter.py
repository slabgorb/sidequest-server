"""Class-scene filter test — qualifying classes only.

Verifies that to_scene_message() filters class-hint choices to only those
whose ClassDef qualifies against the current rolled stats.
"""

from __future__ import annotations

import random

from sidequest.game.builder import CharacterBuilder
from sidequest.genre.models.character import (
    CharCreationChoice,
    CharCreationScene,
    ClassDef,
    MechanicalEffects,
)
from sidequest.genre.models.rules import RulesConfig


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
        ClassDef(
            id="cleric",
            display_name="Cleric",
            rpg_role="healer",
            jungian_default="caregiver",
            prime_requisite="WIS",
            minimum_score=9,
            kit_table="cleric_kit",
        ),
    ]


def _make_class_choice_scene() -> CharCreationScene:
    return CharCreationScene(
        id="the_calling",
        title="What Will You Be?",
        narration="Choose your path.",
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
            CharCreationChoice(
                label="Cleric",
                description="A priest.",
                mechanical_effects=MechanicalEffects(class_hint="Cleric"),
            ),
        ],
    )


def test_class_scene_filters_to_qualifying_only():
    """STR=14, INT=8, WIS=8 → only Fighter qualifies → only Fighter button shown."""
    # Use a two-scene flow: roll scene then class choice.
    # The roll scene uses roll_3d6_strict so _rolled_stats is populated.
    roll_scene = CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="Roll your stats.",
        mechanical_effects=MechanicalEffects(stat_generation="roll_3d6_strict"),
    )
    class_scene = _make_class_choice_scene()
    scenes = [roll_scene, class_scene]
    rules = RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )

    # Script dice to produce STR=14 (dice: 5,4,5), then the rest =8 (2,3,3 × 5).
    # 3 dice per stat × 6 stats = 18 dice.
    # STR = 5+4+5 = 14; DEX = 2+3+3 = 8; CON = 2+3+3 = 8;
    # INT = 2+3+3 = 8; WIS = 2+3+3 = 8; CHA = 2+3+3 = 8.
    scripted_dice = [5, 4, 5] + [2, 3, 3] * 5
    rng = _ScriptedRandom(scripted_dice)
    builder = CharacterBuilder(scenes, rules, rng=rng).with_classes(_make_classes())

    # Advance past the roll scene.
    assert builder.current_scene().id == "the_roll"
    builder.apply_auto_advance()

    # Now on the class-choice scene — get the scene message.
    assert builder.current_scene().id == "the_calling"
    msg = builder.to_scene_message("player1")

    # Only Fighter should be visible — STR=14 qualifies, INT=8 and WIS=8 don't.
    choice_labels = [str(c.label) for c in msg.payload.choices]
    assert choice_labels == ["Fighter"], f"Expected only ['Fighter'], got {choice_labels}"


def test_class_scene_shows_all_when_all_qualify():
    """All stats ≥9 → all classes qualify → all choices shown."""
    roll_scene = CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="Roll your stats.",
        mechanical_effects=MechanicalEffects(stat_generation="roll_3d6_strict"),
    )
    class_scene = _make_class_choice_scene()
    scenes = [roll_scene, class_scene]
    rules = RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )

    # All dice = 3 → all stats = 9 (exactly at minimum_score for all classes).
    rng = _ScriptedRandom([3] * 18)
    builder = CharacterBuilder(scenes, rules, rng=rng).with_classes(_make_classes())

    builder.apply_auto_advance()
    msg = builder.to_scene_message("player1")

    choice_labels = sorted(str(c.label) for c in msg.payload.choices)
    assert choice_labels == ["Cleric", "Fighter", "Mage"]


def test_class_scene_no_filter_without_classes():
    """When no classes are attached, all choices pass through unfiltered."""
    roll_scene = CharCreationScene(
        id="the_roll",
        title="Roll",
        narration="Roll your stats.",
        mechanical_effects=MechanicalEffects(stat_generation="roll_3d6_strict"),
    )
    class_scene = _make_class_choice_scene()
    scenes = [roll_scene, class_scene]
    rules = RulesConfig(
        stat_generation="roll_3d6_strict",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )

    # All 1s → stats = 3 each (nothing qualifies), but no _classes → no filter.
    rng = _ScriptedRandom([1] * 18)
    builder = CharacterBuilder(scenes, rules, rng=rng)
    # Intentionally do NOT call with_classes()

    builder.apply_auto_advance()
    msg = builder.to_scene_message("player1")

    choice_labels = sorted(str(c.label) for c in msg.payload.choices)
    assert choice_labels == ["Cleric", "Fighter", "Mage"]


def test_no_filter_on_mixed_scenes():
    """A scene with only SOME choices carrying class_hint is not filtered."""
    scene = CharCreationScene(
        id="mixed",
        title="Mixed",
        narration="Mixed choices.",
        choices=[
            CharCreationChoice(
                label="Fighter",
                description="A warrior.",
                mechanical_effects=MechanicalEffects(class_hint="Fighter"),
            ),
            CharCreationChoice(
                label="Wanderer",
                description="A wanderer.",
                mechanical_effects=MechanicalEffects(),  # no class_hint
            ),
        ],
    )
    rules = RulesConfig(
        stat_generation="standard_array",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
    )
    builder = CharacterBuilder([scene], rules, rng=random.Random(42)).with_classes(_make_classes())
    # Manually set rolled_stats to ensure filter WOULD fire if not for mixed scene.
    builder._rolled_stats = [("STR", 3), ("DEX", 3), ("CON", 3), ("INT", 3), ("WIS", 3), ("CHA", 3)]

    msg = builder.to_scene_message("player1")

    # Both choices must still be present (not a pure class-hint scene).
    choice_labels = [str(c.label) for c in msg.payload.choices]
    assert "Fighter" in choice_labels
    assert "Wanderer" in choice_labels


class _ScriptedRandom(random.Random):
    """Returns predetermined ints in sequence; falls back to seeded random."""

    def __init__(self, scripted: list[int]):
        super().__init__()
        self._scripted = list(scripted)
        self._fallback = random.Random(42)

    def randint(self, a: int, b: int) -> int:  # type: ignore[override]
        if self._scripted:
            return self._scripted.pop(0)
        return self._fallback.randint(a, b)

    def randrange(self, *args, **kwargs):  # type: ignore[override]
        if self._scripted:
            return self._scripted.pop(0)
        return self._fallback.randrange(*args, **kwargs)
