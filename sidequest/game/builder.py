"""CharacterBuilder — state machine for genre-driven character creation.

Port of sidequest_game::builder (builder.rs, 903 LOC implementation).
ADR-015: builder FSM — builder doesn't exist before new(), conceptually
consumed by build(). No IDLE or COMPLETE states; construction and
consumption are the boundaries.

This module is ported in slices:
- Slice 1 (this commit): pure types — BuilderPhase, SceneInputType,
  SceneResult, AccumulatedChoices, NarrativeHook, HookType, LoreAnchor,
  BuilderError.
- Slice 2: CharacterBuilder core + scene walking.
- Slice 3: stat generation + HP formula.
- Slice 4: build() finalizer.
- Slice 5: wire MechanicalEffects.reputation_bonus + integration test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sidequest.genre.models.character import MechanicalEffects


# ---------------------------------------------------------------------------
# Narrative hook extraction
# ---------------------------------------------------------------------------


class HookType(str, Enum):
    """Category of narrative hook.

    Port of sidequest_game::builder::HookType.
    """

    ORIGIN = "Origin"
    """From race_hint."""
    WOUND = "Wound"
    """From backstory trauma."""
    RELATIONSHIP = "Relationship"
    """From relationship effects."""
    GOAL = "Goal"
    """From goals effects."""
    TRAIT = "Trait"
    """From class_hint or personality_trait."""
    DEBT = "Debt"
    """From obligation effects."""
    SECRET = "Secret"
    """From hidden knowledge."""
    POSSESSION = "Possession"
    """From equipment_hints / item_hint."""


@dataclass
class NarrativeHook:
    """A narrative hook derived from character creation choices.

    Port of sidequest_game::builder::NarrativeHook.
    """

    hook_type: HookType
    source_scene: str
    text: str
    mechanical_key: str | None = None


@dataclass
class LoreAnchor:
    """A connection to the game world (faction, NPC, location).

    Port of sidequest_game::builder::LoreAnchor.

    anchor_type: "faction", "npc_relationship", "location" or similar.
    """

    anchor_type: str
    value: str
    source_scene: str


# ---------------------------------------------------------------------------
# Scene input — tagged union for "how the player responded"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneInputType:
    """Sealed base for scene input variants. Use the concrete subclasses."""


@dataclass(frozen=True)
class ChoiceInput(SceneInputType):
    """Player selected a numbered choice.

    Port of Rust SceneInputType::Choice(usize).
    """

    index: int


@dataclass(frozen=True)
class FreeformInput(SceneInputType):
    """Player typed freeform text.

    Port of Rust SceneInputType::Freeform(String).
    """

    text: str


# ---------------------------------------------------------------------------
# SceneResult — unit of revert for go_back
# ---------------------------------------------------------------------------


@dataclass
class SceneResult:
    """What a single scene produced — the unit of revert.

    Port of sidequest_game::builder::SceneResult.

    choice_description stores the flavor description text from the chosen
    option so we can compose a narrative backstory instead of only keeping
    the mechanical label.
    """

    input_type: SceneInputType
    effects_applied: MechanicalEffects
    hooks_added: list[NarrativeHook] = field(default_factory=list)
    anchors_added: list[LoreAnchor] = field(default_factory=list)
    choice_description: str | None = None


# ---------------------------------------------------------------------------
# AccumulatedChoices — compacted view across all completed scenes
# ---------------------------------------------------------------------------


@dataclass
class AccumulatedChoices:
    """Accumulated mechanical effects across all completed scenes.

    Port of sidequest_game::builder::AccumulatedChoices.

    Most hint fields follow last-one-wins semantics (a later scene overrides
    an earlier one). Lists and stat_bonuses accumulate.

    reputation_bonus wires the Phase 1 IOU (character.py:66) —
    spaghetti_western chargen choices tag reputation_bonus; the builder now
    accumulates it alongside other hints. Downstream reputation system is
    still post-Phase-2; the value simply flows through for now.
    """

    class_hint: str | None = None
    race_hint: str | None = None
    personality_trait: str | None = None
    item_hints: list[str] = field(default_factory=list)
    affinity_hint: str | None = None
    background: str | None = None
    mutation_hint: str | None = None
    training_hint: str | None = None
    emotional_state: str | None = None
    relationship: str | None = None
    goals: str | None = None
    rig_type_hint: str | None = None
    rig_trait: str | None = None
    catch_phrase: str | None = None
    backstory_fragments: list[str] = field(default_factory=list)
    stat_bonuses: dict[str, int] = field(default_factory=dict)
    pronoun_hint: str | None = None
    jungian_hint: str | None = None
    rpg_role_hint: str | None = None
    reputation_bonus: str | None = None


# ---------------------------------------------------------------------------
# BuilderPhase — tagged state machine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuilderPhase:
    """Sealed base for builder phase variants. Use the concrete subclasses."""


@dataclass(frozen=True)
class InProgress(BuilderPhase):
    """Processing genre-defined scenes.

    Port of Rust BuilderPhase::InProgress { scene_index }.
    """

    scene_index: int


@dataclass(frozen=True)
class AwaitingFollowup(BuilderPhase):
    """Scene has a hook_prompt — waiting for player's followup text.

    Port of Rust BuilderPhase::AwaitingFollowup { scene_index, hook_prompt }.
    """

    scene_index: int
    hook_prompt: str


@dataclass(frozen=True)
class Confirmation(BuilderPhase):
    """All scenes done, showing summary for confirmation.

    Port of Rust BuilderPhase::Confirmation.
    """


# Singleton instance of Confirmation — it carries no data, so sharing is fine.
CONFIRMATION: Confirmation = Confirmation()


# ---------------------------------------------------------------------------
# BuilderError — typed exceptions matching Rust BuilderError enum
# ---------------------------------------------------------------------------


class BuilderError(Exception):
    """Base class for CharacterBuilder errors.

    Port of sidequest_game::builder::BuilderError enum. Each Rust variant
    maps to a subclass so callers can catch specific failure modes:

        try:
            builder.apply_choice(idx)
        except BuilderError.InvalidChoice as e:
            ...

    The nested subclass attributes (BuilderError.InvalidChoice, etc.) are
    aliases for the module-level classes; they exist so call sites don't
    need to import every variant separately.
    """


class InvalidChoiceError(BuilderError):
    """Choice index out of range."""

    def __init__(self, index: int, max_index: int) -> None:
        self.index = index
        self.max_index = max_index
        super().__init__(f"invalid choice: index {index} but max is {max_index}")


class WrongPhaseError(BuilderError):
    """Operation not valid in the current phase."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"wrong phase: expected {expected}, got {actual}")


class FreeformNotAllowedError(BuilderError):
    """Freeform input not allowed for this scene."""

    def __init__(self) -> None:
        super().__init__("freeform input not allowed for this scene")


class NoScenesError(BuilderError):
    """No scenes provided to the builder."""

    def __init__(self) -> None:
        super().__init__("no scenes provided")


class CannotRevertError(BuilderError):
    """Cannot revert — already at the first scene."""

    def __init__(self) -> None:
        super().__init__("cannot revert: already at first scene")


class UnknownStatGenerationError(BuilderError):
    """Unrecognized stat generation method."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"unknown stat generation method: {method}")


class InvalidHpFormulaError(BuilderError):
    """HP formula evaluation failed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"hp_formula error: {detail}")


class NumericNameError(BuilderError):
    """Name is purely numeric — likely a UI index, not a real character name.

    Story 30-1: Reject purely numeric names — they indicate a UI choice
    index was used as the name fallback instead of a real character name.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"invalid character name: '{name}' is purely numeric "
            "(likely a UI index, not a name)"
        )


class EdgeConfigMissingClassError(BuilderError):
    """Genre pack declared `edge_config` but omitted a `base_max_by_class`
    entry for the character's class. Fails chargen loudly (story 39-3) —
    silently reverting to the placeholder would hide content bugs.
    """

    def __init__(self, class_name: str) -> None:
        self.class_name = class_name
        super().__init__(
            f"edge_config.base_max_by_class missing entry for class '{class_name}'"
        )


# Attach subclass aliases so callers can write `BuilderError.InvalidChoice`
# in catch blocks, matching the Rust `BuilderError::InvalidChoice { .. }`
# read pattern.
BuilderError.InvalidChoice = InvalidChoiceError  # type: ignore[attr-defined]
BuilderError.WrongPhase = WrongPhaseError  # type: ignore[attr-defined]
BuilderError.FreeformNotAllowed = FreeformNotAllowedError  # type: ignore[attr-defined]
BuilderError.NoScenes = NoScenesError  # type: ignore[attr-defined]
BuilderError.CannotRevert = CannotRevertError  # type: ignore[attr-defined]
BuilderError.UnknownStatGeneration = UnknownStatGenerationError  # type: ignore[attr-defined]
BuilderError.InvalidHpFormula = InvalidHpFormulaError  # type: ignore[attr-defined]
BuilderError.NumericName = NumericNameError  # type: ignore[attr-defined]
BuilderError.EdgeConfigMissingClass = EdgeConfigMissingClassError  # type: ignore[attr-defined]


__all__ = [
    # Hooks and anchors
    "HookType",
    "NarrativeHook",
    "LoreAnchor",
    # Scene input variants
    "SceneInputType",
    "ChoiceInput",
    "FreeformInput",
    # Scene result and accumulation
    "SceneResult",
    "AccumulatedChoices",
    # Phase state machine
    "BuilderPhase",
    "InProgress",
    "AwaitingFollowup",
    "Confirmation",
    "CONFIRMATION",
    # Errors
    "BuilderError",
    "InvalidChoiceError",
    "WrongPhaseError",
    "FreeformNotAllowedError",
    "NoScenesError",
    "CannotRevertError",
    "UnknownStatGenerationError",
    "InvalidHpFormulaError",
    "NumericNameError",
    "EdgeConfigMissingClassError",
]
