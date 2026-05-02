"""Tests for sidequest.game.builder — Slice 2: CharacterBuilder core + scene walking.

Ports behavior tests for construction, phase queries, scene-walking
operations (apply_choice / apply_freeform / answer_followup /
apply_auto_advance / go_back / go_to_scene / revert), and the
accumulated() view.

Out of scope for this test file:
- Stat generation (Slice 3) — generate_stats / roll_3d6 / point_buy
- HP formula evaluation (Slice 3)
- build() finalizer (Slice 4)
- to_scene_message / OTEL watcher events (Slice 4)
"""

from __future__ import annotations

import pytest

from sidequest.game.builder import (
    AccumulatedChoices,
    CannotRevertError,
    CharacterBuilder,
    FreeformNotAllowedError,
    HookType,
    InvalidChoiceError,
    LoreAnchor,
    NoScenesError,
    WrongPhaseError,
    extract_anchors,
    extract_hooks,
    humanize_snake_case,
    strip_unmatched_placeholders,
)
from sidequest.genre.models.character import (
    CharCreationChoice,
    CharCreationScene,
    MechanicalEffects,
)
from sidequest.genre.models.rules import RulesConfig

# ---------------------------------------------------------------------------
# Helpers for constructing fixtures
# ---------------------------------------------------------------------------


def make_choice(
    label: str,
    description: str = "A description",
    **effect_fields: object,
) -> CharCreationChoice:
    """Build a CharCreationChoice with the given label + mechanical effect fields."""
    return CharCreationChoice(
        label=label,
        description=description,
        mechanical_effects=MechanicalEffects(**effect_fields),  # type: ignore[arg-type]
    )


def make_scene(
    scene_id: str,
    *,
    choices: list[CharCreationChoice] | None = None,
    allows_freeform: bool | None = None,
    hook_prompt: str | None = None,
    mechanical_effects: MechanicalEffects | None = None,
    narration: str = "Scene narration.",
    title: str = "Scene title",
) -> CharCreationScene:
    """Build a CharCreationScene with sensible defaults."""
    return CharCreationScene(
        id=scene_id,
        title=title,
        narration=narration,
        choices=choices or [],
        allows_freeform=allows_freeform,
        hook_prompt=hook_prompt,
        mechanical_effects=mechanical_effects,
    )


def simple_rules() -> RulesConfig:
    """A minimal RulesConfig that satisfies the builder constructor."""
    return RulesConfig(
        stat_generation="standard_array",
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
        point_buy_budget=27,
        default_class="Fighter",
        default_race="Human",
    )


def two_choice_scenes() -> list[CharCreationScene]:
    """Two simple scenes: origin picker + class picker. No freeform, no hooks."""
    return [
        make_scene(
            "origin",
            choices=[
                make_choice(
                    "Mutant",
                    description="A wanderer born in the ash.",
                    race_hint="Mutant",
                ),
                make_choice(
                    "Human",
                    description="A survivor from before the fall.",
                    race_hint="Human",
                ),
            ],
        ),
        make_scene(
            "class",
            choices=[
                make_choice(
                    "Ranger",
                    description="A hunter who reads the land.",
                    class_hint="Ranger",
                ),
                make_choice(
                    "Scribe",
                    description="A keeper of old knowledge.",
                    class_hint="Scribe",
                ),
            ],
        ),
    ]


# ===========================================================================
# Construction
# ===========================================================================


class TestConstruction:
    def test_empty_scenes_raises_no_scenes(self) -> None:
        with pytest.raises(NoScenesError):
            CharacterBuilder(scenes=[], rules=simple_rules())

    def test_starts_in_progress_at_scene_zero(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        assert b.is_in_progress()
        assert b.current_scene_index() == 0
        assert b.total_scenes() == 2

    def test_rules_config_fallbacks(self) -> None:
        """race_label / class_label default to 'Race' / 'Class' when absent."""
        rules = RulesConfig(stat_generation="standard_array")
        scenes = [make_scene("only", choices=[make_choice("Go")])]
        b = CharacterBuilder(scenes=scenes, rules=rules)
        assert b.race_label() == "Race"
        assert b.class_label() == "Class"

    def test_genre_specific_labels(self) -> None:
        rules = simple_rules()
        rules.race_label = "Species"
        rules.class_label = "Archetype"
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=rules)
        assert b.race_label() == "Species"
        assert b.class_label() == "Archetype"

    def test_default_class_accessible(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        assert b.default_class() == "Fighter"

    def test_rolled_stats_none_in_slice_2(self) -> None:
        """Slice 2 never rolls — reserved field reads as None."""
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        assert b.rolled_stats() is None

    def test_scenes_returns_a_copy(self) -> None:
        """scenes() must not leak internal state — mutation is local to the
        caller."""
        scenes = two_choice_scenes()
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        returned = b.scenes()
        returned.clear()
        assert b.total_scenes() == 2


# ===========================================================================
# Fluent setters
# ===========================================================================


class TestFluentSetters:
    def test_with_lobby_name_stores_trimmed(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules()).with_lobby_name(
            "  Kara  "
        )
        # Indirect check via character_name fallback semantics is post-Slice 2;
        # for Slice 2 just verify the attribute is set via internal state.
        assert b._lobby_name == "Kara"

    def test_with_lobby_name_blank_clears(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules()).with_lobby_name(
            "   "
        )
        assert b._lobby_name is None

    def test_fluent_chains(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules()).with_lobby_name(
            "Kara"
        )
        assert isinstance(b, CharacterBuilder)


# ===========================================================================
# apply_choice
# ===========================================================================


class TestApplyChoice:
    def test_advances_to_next_scene(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        assert b.is_in_progress()
        assert b.current_scene_index() == 1

    def test_last_scene_transitions_to_confirmation(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.is_confirmation()

    def test_index_out_of_range_raises(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(InvalidChoiceError) as excinfo:
            b.apply_choice(5)
        assert excinfo.value.index == 5
        assert excinfo.value.max_index == 1  # 2 choices, max index 1

    def test_wrong_phase_confirmation_raises(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        with pytest.raises(WrongPhaseError) as excinfo:
            b.apply_choice(0)
        assert excinfo.value.actual == "Confirmation"

    def test_hook_prompt_transitions_to_awaiting_followup(self) -> None:
        scenes = [
            make_scene(
                "wound",
                choices=[make_choice("A scar", background="scarred")],
                hook_prompt="Tell me about the scar.",
            )
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        assert b.is_awaiting_followup()
        assert b.current_hook_prompt() == "Tell me about the scar."
        assert b.current_scene_index() == 0  # still on the same scene

    def test_applies_mechanical_effects(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)  # Mutant
        b.apply_choice(0)  # Ranger
        acc = b.accumulated()
        assert acc.race_hint == "Mutant"
        assert acc.class_hint == "Ranger"

    def test_records_choice_description(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)  # description: "A wanderer born in the ash."
        results = b.scene_results()
        assert results[0].choice_description == "A wanderer born in the ash."

    def test_empty_choices_max_index_zero(self) -> None:
        """saturating_sub parity — a scene with zero choices should report
        max_index=0 (not -1) in the raised InvalidChoiceError."""
        scenes = [make_scene("empty", allows_freeform=False)]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        with pytest.raises(InvalidChoiceError) as excinfo:
            b.apply_choice(0)
        assert excinfo.value.max_index == 0


# ===========================================================================
# apply_freeform
# ===========================================================================


class TestApplyFreeform:
    def test_name_entry_scene_accepts_freeform(self) -> None:
        """Empty choices + allows_freeform=True is the canonical name-entry scene."""
        scenes = [
            make_scene("class", choices=[make_choice("Ranger", class_hint="Ranger")]),
            make_scene("name", allows_freeform=True),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_freeform("Kara")
        assert b.is_confirmation()
        assert b.character_name() == "Kara"

    def test_scene_with_choices_rejects_freeform(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(FreeformNotAllowedError):
            b.apply_freeform("arbitrary text")

    def test_awaiting_followup_rejects_freeform(self) -> None:
        scenes = [
            make_scene(
                "wound",
                choices=[make_choice("Scar")],
                hook_prompt="Describe it.",
            )
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        assert b.is_awaiting_followup()
        with pytest.raises(WrongPhaseError) as excinfo:
            b.apply_freeform("a long scar")
        assert excinfo.value.actual == "AwaitingFollowup"

    def test_applies_scene_level_effects(self) -> None:
        """Scene-level mechanical_effects (e.g. name scene with pronoun_hint)
        are recorded on the result."""
        scenes = [
            make_scene(
                "name",
                allows_freeform=True,
                mechanical_effects=MechanicalEffects(pronoun_hint="she/her"),
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_freeform("Kara")
        acc = b.accumulated()
        assert acc.pronoun_hint == "she/her"


# ===========================================================================
# answer_followup
# ===========================================================================


class TestAnswerFollowup:
    def test_followup_inserts_wound_hook_at_position_zero(self) -> None:
        scenes = [
            make_scene(
                "wound",
                choices=[make_choice("Scar", background="scarred")],
                hook_prompt="Describe it.",
            )
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.answer_followup("A thin white line across her palm.")
        results = b.scene_results()
        # First hook is the wound from the followup answer.
        assert results[0].hooks_added[0].hook_type == HookType.WOUND
        assert results[0].hooks_added[0].text == "A thin white line across her palm."
        # Source scene matches.
        assert results[0].hooks_added[0].source_scene == "wound"

    def test_followup_advances_scene(self) -> None:
        scenes = [
            make_scene(
                "wound",
                choices=[make_choice("Scar")],
                hook_prompt="Describe it.",
            ),
            make_scene("class", choices=[make_choice("Ranger", class_hint="Ranger")]),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.answer_followup("A long story.")
        assert b.is_in_progress()
        assert b.current_scene_index() == 1

    def test_wrong_phase_raises(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(WrongPhaseError) as excinfo:
            b.answer_followup("anything")
        assert excinfo.value.expected == "AwaitingFollowup"


# ===========================================================================
# apply_auto_advance
# ===========================================================================


class TestApplyAutoAdvance:
    def test_advances_choiceless_scene(self) -> None:
        scenes = [
            make_scene("display", allows_freeform=False),
            make_scene("class", choices=[make_choice("Ranger", class_hint="Ranger")]),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_auto_advance()
        assert b.is_in_progress()
        assert b.current_scene_index() == 1

    def test_rejects_scene_with_choices(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(InvalidChoiceError):
            b.apply_auto_advance()

    def test_rejects_freeform_scene(self) -> None:
        """A name-entry scene requires freeform, not auto-advance."""
        scenes = [make_scene("name", allows_freeform=True)]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        with pytest.raises(InvalidChoiceError):
            b.apply_auto_advance()

    def test_applies_scene_level_effects(self) -> None:
        scenes = [
            make_scene(
                "display",
                allows_freeform=False,
                mechanical_effects=MechanicalEffects(background="Postwar survivor."),
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_auto_advance()
        acc = b.accumulated()
        assert acc.background == "Postwar survivor."


# ===========================================================================
# go_back / go_to_scene / revert — the state-machine invariant tests
# ===========================================================================


class TestGoBack:
    def test_reverts_last_result(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)  # Mutant
        b.apply_choice(0)  # Ranger
        assert b.is_confirmation()
        b.go_back()
        assert b.is_in_progress()
        assert b.current_scene_index() == 1
        # The Ranger pick is gone; accumulated should no longer have class_hint.
        acc = b.accumulated()
        assert acc.race_hint == "Mutant"  # still from scene 0
        assert acc.class_hint is None  # reverted

    def test_go_back_from_first_scene_raises(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(WrongPhaseError):
            b.go_back()

    def test_go_back_then_forward_replays_different_choice(self) -> None:
        """The classic revert-and-pick-differently flow — accumulated state
        must reflect the *new* pick, not the reverted one."""
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)  # Mutant
        b.apply_choice(0)  # Ranger
        b.go_back()
        b.apply_choice(1)  # Scribe instead
        acc = b.accumulated()
        assert acc.class_hint == "Scribe"

    def test_go_back_reverts_hooks(self) -> None:
        """Hooks attached to the reverted scene result must be gone."""
        scenes = [
            make_scene(
                "origin",
                choices=[make_choice("Mutant", race_hint="Mutant")],
            ),
            make_scene(
                "class",
                choices=[make_choice("Ranger", class_hint="Ranger")],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        # 2 scene results, each with a hook on it.
        assert len(b.scene_results()) == 2
        b.go_back()
        assert len(b.scene_results()) == 1


class TestGoToScene:
    def test_jumps_to_earlier_scene(self) -> None:
        scenes = two_choice_scenes() + [
            make_scene("name", allows_freeform=True),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        b.apply_freeform("Kara")
        assert b.is_confirmation()
        b.go_to_scene(0)
        assert b.is_in_progress()
        assert b.current_scene_index() == 0
        # All three results discarded.
        assert b.scene_results() == []

    def test_out_of_range_target_raises(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(WrongPhaseError):
            b.go_to_scene(5)

    def test_target_equal_to_length_raises(self) -> None:
        """scene index == len(scenes) is the confirmation boundary — not
        a valid scene to jump to."""
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(WrongPhaseError):
            b.go_to_scene(2)


class TestRevert:
    def test_revert_pops_and_returns_to_prior(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        b.revert()
        assert b.is_in_progress()
        assert b.current_scene_index() == 0
        assert b.scene_results() == []

    def test_revert_from_empty_raises_cannot_revert(self) -> None:
        """Revert from empty raises CannotRevertError — distinct variant
        from go_back's WrongPhaseError. The error variants are a named API."""
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        with pytest.raises(CannotRevertError):
            b.revert()


# ===========================================================================
# accumulated() — last-one-wins and multi-value accumulation
# ===========================================================================


class TestAccumulated:
    def test_empty_history_is_default(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        acc = b.accumulated()
        assert acc == AccumulatedChoices()

    def test_last_one_wins_for_single_value_hints(self) -> None:
        """Two scenes both set class_hint; the second wins."""
        scenes = [
            make_scene(
                "pick1",
                choices=[make_choice("A", class_hint="Ranger")],
            ),
            make_scene(
                "pick2",
                choices=[make_choice("B", class_hint="Scribe")],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.accumulated().class_hint == "Scribe"

    def test_item_hints_accumulate(self) -> None:
        scenes = [
            make_scene("a", choices=[make_choice("A", item_hint="crowbar")]),
            make_scene("b", choices=[make_choice("B", item_hint="compass")]),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.accumulated().item_hints == ["crowbar", "compass"]

    def test_item_hint_none_sentinel_is_filtered(self) -> None:
        """Rust filters item_hint == 'none' and empty string — mirror that."""
        scenes = [
            make_scene("a", choices=[make_choice("A", item_hint="none")]),
            make_scene("b", choices=[make_choice("B", item_hint="")]),
            make_scene("c", choices=[make_choice("C", item_hint="crowbar")]),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.accumulated().item_hints == ["crowbar"]

    def test_stat_bonuses_accumulate_additively(self) -> None:
        scenes = [
            make_scene(
                "a",
                choices=[make_choice("A", stat_bonuses={"STR": 2, "DEX": 1})],
            ),
            make_scene(
                "b",
                choices=[make_choice("B", stat_bonuses={"STR": 1, "CON": 2})],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        acc = b.accumulated()
        assert acc.stat_bonuses == {"STR": 3, "DEX": 1, "CON": 2}

    def test_backstory_fragments_collect_descriptions(self) -> None:
        """Choice descriptions flow into backstory_fragments in scene order."""
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        acc = b.accumulated()
        assert acc.backstory_fragments == [
            "A wanderer born in the ash.",
            "A hunter who reads the land.",
        ]

    def test_pronoun_only_choice_excluded_from_backstory(self) -> None:
        """A pronoun-only pick (e.g. 'He.') must not leak into backstory_fragments.
        Reviewer finding from story 31-2."""
        scenes = [
            make_scene(
                "pronoun",
                choices=[make_choice("He.", description="He.", pronoun_hint="he/him")],
            ),
            make_scene(
                "class",
                choices=[
                    make_choice(
                        "Ranger",
                        description="A hunter who reads the land.",
                        class_hint="Ranger",
                    )
                ],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        acc = b.accumulated()
        # Pronoun still wins on the dedicated field...
        assert acc.pronoun_hint == "he/him"
        # ...but "He." does NOT contaminate the backstory fragments.
        assert acc.backstory_fragments == ["A hunter who reads the land."]

    def test_pronoun_with_other_hint_is_not_filtered(self) -> None:
        """If the pronoun pick also carries a class/race/etc. hint, the
        description is narrative-bearing and survives."""
        scenes = [
            make_scene(
                "combo",
                choices=[
                    make_choice(
                        "She, a Ranger.",
                        description="the armed woman with murder in her eyes",
                        pronoun_hint="she/her",
                        class_hint="Ranger",
                    )
                ],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        acc = b.accumulated()
        assert acc.backstory_fragments == [
            "the armed woman with murder in her eyes",
        ]

    def test_reputation_bonus_accumulates(self) -> None:
        """Phase 1 IOU wired: reputation_bonus on a chargen choice flows
        into the accumulated view. docs/plans/phase-2-chargen-port.md."""
        scenes = [
            make_scene(
                "reputation",
                choices=[
                    make_choice(
                        "The silent one",
                        reputation_bonus="intimidation",
                    ),
                ],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        acc = b.accumulated()
        assert acc.reputation_bonus == "intimidation"

    def test_reputation_bonus_last_one_wins(self) -> None:
        """Single-value field — later scenes override earlier."""
        scenes = [
            make_scene(
                "a",
                choices=[make_choice("A", reputation_bonus="intimidation")],
            ),
            make_scene(
                "b",
                choices=[make_choice("B", reputation_bonus="stealth")],
            ),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.accumulated().reputation_bonus == "stealth"


# ===========================================================================
# Hook / anchor extraction (module helpers)
# ===========================================================================


class TestExtractHooks:
    def test_race_hint_produces_origin_hook(self) -> None:
        hooks = extract_hooks("origin", MechanicalEffects(race_hint="Mutant"))
        assert len(hooks) == 1
        assert hooks[0].hook_type == HookType.ORIGIN
        assert hooks[0].text == "Origin: Mutant"
        assert hooks[0].mechanical_key == "race_hint"

    def test_class_hint_produces_trait_hook(self) -> None:
        hooks = extract_hooks("class", MechanicalEffects(class_hint="Ranger"))
        assert hooks[0].hook_type == HookType.TRAIT
        assert hooks[0].text == "Class: Ranger"

    def test_multiple_hints_produce_multiple_hooks(self) -> None:
        hooks = extract_hooks(
            "combo",
            MechanicalEffects(
                race_hint="Mutant",
                class_hint="Ranger",
                personality_trait="wary",
                relationship="sister missing",
                goals="find her",
                item_hint="crowbar",
            ),
        )
        assert len(hooks) == 6
        types = {h.hook_type for h in hooks}
        assert HookType.ORIGIN in types
        assert HookType.TRAIT in types  # class + personality both map to TRAIT
        assert HookType.RELATIONSHIP in types
        assert HookType.GOAL in types
        assert HookType.POSSESSION in types

    def test_no_hints_produces_no_hooks(self) -> None:
        hooks = extract_hooks("empty", MechanicalEffects())
        assert hooks == []

    def test_source_scene_preserved(self) -> None:
        hooks = extract_hooks("origin_scene", MechanicalEffects(race_hint="Mutant"))
        assert all(h.source_scene == "origin_scene" for h in hooks)


class TestExtractAnchors:
    def test_relationship_produces_npc_anchor(self) -> None:
        anchors = extract_anchors("rel_scene", MechanicalEffects(relationship="sister missing"))
        assert anchors == [
            LoreAnchor(
                anchor_type="npc",
                value="sister missing",
                source_scene="rel_scene",
            )
        ]

    def test_no_relationship_produces_no_anchors(self) -> None:
        anchors = extract_anchors("s", MechanicalEffects(class_hint="Ranger"))
        assert anchors == []


# ===========================================================================
# humanize_snake_case
# ===========================================================================


class TestHumanizeSnakeCase:
    @pytest.mark.parametrize(
        "snake,expected",
        [
            ("natural_armor", "Natural Armor"),
            ("mystery_compass", "Mystery Compass"),
            ("single", "Single"),
            ("", ""),
            ("a_b_c", "A B C"),
            ("already_Capitalized", "Already Capitalized"),
        ],
    )
    def test_conversion(self, snake: str, expected: str) -> None:
        assert humanize_snake_case(snake) == expected


# ===========================================================================
# strip_unmatched_placeholders
# ===========================================================================


class TestStripUnmatchedPlaceholders:
    def test_plain_text_unchanged(self) -> None:
        assert strip_unmatched_placeholders("A ranger of the wastes") == "A ranger of the wastes"

    def test_drops_orphan_placeholder(self) -> None:
        """The literal {feature} must not leak to the player."""
        s = "Former ratcatcher. {feature}. Now a drifter."
        out = strip_unmatched_placeholders(s)
        assert "{feature}" not in out
        # Orphan punctuation/whitespace after the placeholder is cleaned up.
        assert out == "Former ratcatcher. Now a drifter."

    def test_multiple_orphans(self) -> None:
        s = "{a}, {b}. {c} crowbar."
        out = strip_unmatched_placeholders(s)
        assert "{" not in out
        assert "crowbar" in out

    def test_unbalanced_brace_preserved(self) -> None:
        """Unbalanced { (no closing }) stays literal so the bug is visible."""
        s = "A {feature of the past"
        out = strip_unmatched_placeholders(s)
        assert "{" in out

    def test_whitespace_collapsed(self) -> None:
        s = "A    ranger\tof  the wastes"
        out = strip_unmatched_placeholders(s)
        assert out == "A ranger of the wastes"


# ===========================================================================
# character_name lobby-name semantics check
# ===========================================================================


class TestCharacterName:
    def test_reads_from_last_scene_freeform(self) -> None:
        scenes = [
            make_scene("class", choices=[make_choice("Ranger", class_hint="Ranger")]),
            make_scene("name", allows_freeform=True),
        ]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_choice(0)
        b.apply_freeform("  Kara  ")
        assert b.character_name() == "Kara"

    def test_returns_none_when_last_scene_has_choices(self) -> None:
        b = CharacterBuilder(scenes=two_choice_scenes(), rules=simple_rules())
        b.apply_choice(0)
        b.apply_choice(0)
        assert b.character_name() is None

    def test_returns_none_when_no_results(self) -> None:
        scenes = [make_scene("name", allows_freeform=True)]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        assert b.character_name() is None

    def test_returns_none_when_freeform_blank(self) -> None:
        scenes = [make_scene("name", allows_freeform=True)]
        b = CharacterBuilder(scenes=scenes, rules=simple_rules())
        b.apply_freeform("   ")
        assert b.character_name() is None
