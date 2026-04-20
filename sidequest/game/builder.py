"""CharacterBuilder — state machine for genre-driven character creation.

Port of sidequest_game::builder (builder.rs, 903 LOC implementation).
ADR-015: builder FSM — builder doesn't exist before new(), conceptually
consumed by build(). No IDLE or COMPLETE states; construction and
consumption are the boundaries.

This module is ported in slices:
- Slice 1: pure types — BuilderPhase, SceneInputType, SceneResult,
  AccumulatedChoices, NarrativeHook, HookType, LoreAnchor, BuilderError.
- Slice 2 (this commit): CharacterBuilder core + scene walking +
  accumulated() + extract_hooks/anchors + helper formatters.
- Slice 3: stat generation + HP formula.
- Slice 4: build() finalizer + to_scene_message + OTEL watcher events.
- Slice 5: integration test walking a real genre pack end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sidequest.genre.models.character import (
    BackstoryTables,
    CharCreationScene,
    EquipmentTables,
    MechanicalEffects,
)
from sidequest.genre.models.rules import EdgeConfig, RulesConfig


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


# ---------------------------------------------------------------------------
# Hook / anchor extraction (pure helpers)
# ---------------------------------------------------------------------------


def extract_hooks(scene_id: str, effects: MechanicalEffects) -> list[NarrativeHook]:
    """Derive narrative hooks from mechanical effects on a chosen option.

    Port of sidequest_game::builder::extract_hooks (module-private in Rust).
    Each produced hook records the mechanical_key that generated it so the
    build() finalizer can filter hooks already represented on the character
    sheet (race, class, personality).
    """
    hooks: list[NarrativeHook] = []

    if effects.race_hint is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.ORIGIN,
                source_scene=scene_id,
                text=f"Origin: {effects.race_hint}",
                mechanical_key="race_hint",
            )
        )

    if effects.class_hint is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.TRAIT,
                source_scene=scene_id,
                text=f"Class: {effects.class_hint}",
                mechanical_key="class_hint",
            )
        )

    if effects.personality_trait is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.TRAIT,
                source_scene=scene_id,
                text=f"Personality: {effects.personality_trait}",
                mechanical_key="personality_trait",
            )
        )

    if effects.relationship is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.RELATIONSHIP,
                source_scene=scene_id,
                text=f"Relationship: {effects.relationship}",
                mechanical_key="relationship",
            )
        )

    if effects.goals is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.GOAL,
                source_scene=scene_id,
                text=f"Goal: {effects.goals}",
                mechanical_key="goals",
            )
        )

    if effects.item_hint is not None:
        hooks.append(
            NarrativeHook(
                hook_type=HookType.POSSESSION,
                source_scene=scene_id,
                text=f"Item: {effects.item_hint}",
                mechanical_key="item_hint",
            )
        )

    return hooks


def extract_anchors(scene_id: str, effects: MechanicalEffects) -> list[LoreAnchor]:
    """Derive lore anchors (world-graph links) from mechanical effects.

    Port of sidequest_game::builder::extract_anchors (module-private in
    Rust). Relationship effects imply NPC anchors — if the choice names a
    mentor or rival, that name becomes a future lore seed.
    """
    anchors: list[LoreAnchor] = []
    if effects.relationship is not None:
        anchors.append(
            LoreAnchor(
                anchor_type="npc",
                value=effects.relationship,
                source_scene=scene_id,
            )
        )
    return anchors


# ---------------------------------------------------------------------------
# String helpers (module-level, pure)
# ---------------------------------------------------------------------------


def humanize_snake_case(s: str) -> str:
    """Convert a snake_case identifier to Title Case display name.

    Port of sidequest_game::builder::humanize_snake_case.
    E.g. "natural_armor" → "Natural Armor",
         "mystery_compass" → "Mystery Compass".
    """
    return " ".join(word.capitalize() if word else "" for word in s.split("_"))


def strip_unmatched_placeholders(s: str) -> str:
    """Strip any unmatched `{key}` placeholders and orphan trailing
    punctuation/whitespace from a substituted template.

    Port of sidequest_game::builder::strip_unmatched_placeholders.

    After a template has had every known table key substituted, any
    remaining `{key}` placeholders correspond to keys the genre pack didn't
    supply. The literal "{feature}" would otherwise leak into user-facing
    prose. Drop the placeholder and consume any immediately-following `. `,
    `, `, or bare whitespace so we don't leave "Former ratcatcher. . ." in
    the output.

    Unbalanced placeholders (no closing `}`) preserve the literal `{` so
    the bug is visible rather than silently swallowed — SOUL.md: "Fail
    loud at the boundary."

    Reviewer finding from story 31-2.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c != "{":
            out.append(c)
            i += 1
            continue
        # Skip to the matching '}' (or end of string if unbalanced).
        close = s.find("}", i + 1)
        if close == -1:
            # Unbalanced — keep the literal '{' and stop scanning.
            out.append("{")
            break
        # Advance past the '}' and eat orphan trailing punctuation/whitespace.
        i = close + 1
        while i < n and s[i] in (".", ",", " "):
            i += 1

    # Collapse internal whitespace runs and trim leading/trailing whitespace.
    return " ".join("".join(out).split())


# ---------------------------------------------------------------------------
# CharacterBuilder — the state machine
# ---------------------------------------------------------------------------


class CharacterBuilder:
    """State machine for character creation driven by genre-pack scenes.

    Port of sidequest_game::builder::CharacterBuilder. Tracks scene
    progression, accumulates mechanical effects, extracts narrative hooks,
    and ultimately produces a Character (build() lands in Slice 4).

    Slice 2 scope: construction, phase queries, scene walking
    (apply_choice / apply_freeform / answer_followup / apply_auto_advance /
    go_back / go_to_scene / revert), accumulated() view computation.

    Out of scope for Slice 2: stat_generation (Slice 3), hp_formula
    evaluation (Slice 3), build() finalizer (Slice 4), to_scene_message
    protocol rendering (Slice 4), scene narration interpolation + OTEL
    watcher events (Slice 4). Rolling of 3d6 strict stats at construction
    is also deferred to Slice 3 — the rolled_stats field is reserved and
    reads as None in Slice 2.
    """

    def __init__(
        self,
        scenes: list[CharCreationScene],
        rules: RulesConfig,
        backstory_tables: BackstoryTables | None = None,
    ) -> None:
        """Create a new builder.

        Raises NoScenesError if `scenes` is empty. The Rust API exposed a
        panicking `new` and a fallible `try_new`; Python collapses those to
        a single constructor that raises — matching Python's exception-
        first idiom and removing a duplicate code path.
        """
        if not scenes:
            raise NoScenesError()

        self._scenes: list[CharCreationScene] = scenes
        self._results: list[SceneResult] = []
        self._phase: BuilderPhase = InProgress(scene_index=0)

        # Configuration sourced from RulesConfig. Keep these as attributes
        # (not a stored reference) so later slices can mutate stat_generation
        # when a scene directive overrides the default (see Rust
        # apply_freeform handling of scene-level stat_generation).
        self._stat_generation: str = rules.stat_generation
        self._ability_score_names: list[str] = list(rules.ability_score_names)
        self._default_class: str | None = rules.default_class
        self._default_race: str | None = rules.default_race
        self._default_hp: int | None = rules.default_hp
        self._default_ac: int | None = rules.default_ac
        self._class_hp_bases: dict[str, int] = dict(rules.class_hp_bases)
        self._hp_formula: str | None = rules.hp_formula
        self._edge_config: EdgeConfig | None = rules.edge_config
        self._point_buy_budget: int = rules.point_buy_budget
        self._race_label: str = rules.race_label or "Race"
        self._class_label: str = rules.class_label or "Class"

        # Stat rolling at construction lands in Slice 3. Reserved field.
        self._rolled_stats: list[tuple[str, int]] | None = None

        self._backstory_tables: BackstoryTables | None = backstory_tables
        self._equipment_tables: EquipmentTables | None = None
        self._lobby_name: str | None = None

    # --- Fluent setters ---

    def with_lobby_name(self, name: str) -> "CharacterBuilder":
        """Attach the lobby-provided player name.

        Used as a fallback for the `{name}` placeholder in scene narration
        when the genre has no name-entry scene (heavy_metal,
        caverns_and_claudes). Fluent setter — chain after construction.

        Blank / whitespace-only names clear the attribute so interpolation
        falls through to the scene-entered name.
        """
        trimmed = name.strip()
        self._lobby_name = trimmed if trimmed else None
        return self

    def with_equipment_tables(self, tables: EquipmentTables) -> "CharacterBuilder":
        """Attach random equipment tables.

        When set AND a scene declares `equipment_generation: random_table`,
        the Slice 4 build() finalizer will roll starting inventory from
        these tables. Story 31-3.
        """
        self._equipment_tables = tables
        return self

    # --- Phase queries ---

    def is_in_progress(self) -> bool:
        """Whether the builder is in InProgress phase."""
        return isinstance(self._phase, InProgress)

    def is_awaiting_followup(self) -> bool:
        """Whether the builder is awaiting a followup answer."""
        return isinstance(self._phase, AwaitingFollowup)

    def is_confirmation(self) -> bool:
        """Whether the builder is in Confirmation phase."""
        return isinstance(self._phase, Confirmation)

    def current_scene_index(self) -> int:
        """Current scene index (0-based). Returns len(scenes) at Confirmation."""
        match self._phase:
            case InProgress(scene_index=i):
                return i
            case AwaitingFollowup(scene_index=i):
                return i
            case Confirmation():
                return len(self._scenes)
            case _:  # pragma: no cover — exhaustive
                raise AssertionError(f"unknown phase: {self._phase!r}")

    def current_scene(self) -> CharCreationScene:
        """Reference to the current scene definition.

        In Confirmation phase the builder is past the last scene; callers
        should branch on is_confirmation() before reading current_scene().
        """
        return self._scenes[self.current_scene_index()]

    def total_scenes(self) -> int:
        """Total number of scenes."""
        return len(self._scenes)

    def scenes(self) -> list[CharCreationScene]:
        """The raw scene definitions (used for lore seeding).

        Returns a shallow copy so callers cannot mutate builder state.
        """
        return list(self._scenes)

    def scene_results(self) -> list[SceneResult]:
        """The accumulated scene results stack.

        Returns a shallow copy so callers cannot mutate builder state.
        """
        return list(self._results)

    def current_hook_prompt(self) -> str | None:
        """Get the current hook prompt text, if awaiting followup."""
        if isinstance(self._phase, AwaitingFollowup):
            return self._phase.hook_prompt
        return None

    def rolled_stats(self) -> list[tuple[str, int]] | None:
        """Pre-rolled stats from roll_3d6_strict generation, if any.

        Exposed so external renderers (e.g. the confirmation summary
        composer) can read stats without reaching into private fields.

        Slice 3 wires the actual roll at construction — this reads None
        in Slice 2.
        """
        return list(self._rolled_stats) if self._rolled_stats is not None else None

    def race_label(self) -> str:
        """Genre-specific label for the "race" field (e.g., "Species", "Origin")."""
        return self._race_label

    def class_label(self) -> str:
        """Genre-specific label for the "class" field (e.g., "Archetype", "Path")."""
        return self._class_label

    def default_class(self) -> str | None:
        """Default class from the genre pack's rules, if defined.

        Used by external renderers to resolve starting equipment when
        chargen doesn't set an explicit class_hint.
        """
        return self._default_class

    def character_name(self) -> str | None:
        """Extract the character name from the name-entry scene.

        The name scene is the last scene with no choices — if the player
        typed freeform text there, that's the name. Blank text falls
        through to None so callers can substitute the lobby name.
        """
        if not self._scenes:
            return None
        last_scene = self._scenes[-1]
        if last_scene.choices:
            return None
        if not self._results:
            return None
        last_result = self._results[-1]
        if not isinstance(last_result.input_type, FreeformInput):
            return None
        trimmed = last_result.input_type.text.strip()
        return trimmed if trimmed else None

    # --- Accumulated view ---

    def accumulated(self) -> AccumulatedChoices:
        """Compute accumulated choices from scene results.

        Most hint fields follow last-one-wins (a later scene overrides an
        earlier one). Lists and stat_bonuses accumulate additively.

        Port of sidequest_game::builder::CharacterBuilder::accumulated.

        The reputation_bonus accumulation here closes the Phase 1 IOU
        from docs/plans/phase-2-chargen-port.md — the field was accepted
        as pass-through on MechanicalEffects in Phase 1; this is the
        first consumer. Last-one-wins like other single-value hints.

        The pronoun-only-choice filter for backstory_fragments excludes
        "He.", "She.", etc. — single-token pronoun picks that aren't
        narrative-bearing. Any other hint field on the same result
        re-qualifies the fragment so meaningful descriptions like "the
        armed woman with murder in her eyes" survive (reviewer finding
        from story 31-2).
        """
        acc = AccumulatedChoices()
        for result in self._results:
            eff = result.effects_applied

            # Single-value hints — last one wins.
            if eff.class_hint is not None:
                acc.class_hint = eff.class_hint
            if eff.race_hint is not None:
                acc.race_hint = eff.race_hint
            if eff.personality_trait is not None:
                acc.personality_trait = eff.personality_trait
            if eff.affinity_hint is not None:
                acc.affinity_hint = eff.affinity_hint
            if eff.background is not None:
                acc.background = eff.background
            if eff.mutation_hint is not None:
                acc.mutation_hint = eff.mutation_hint
            if eff.training_hint is not None:
                acc.training_hint = eff.training_hint
            if eff.emotional_state is not None:
                acc.emotional_state = eff.emotional_state
            if eff.relationship is not None:
                acc.relationship = eff.relationship
            if eff.goals is not None:
                acc.goals = eff.goals
            if eff.rig_type_hint is not None:
                acc.rig_type_hint = eff.rig_type_hint
            if eff.rig_trait is not None:
                acc.rig_trait = eff.rig_trait
            if eff.catch_phrase is not None:
                acc.catch_phrase = eff.catch_phrase
            if eff.pronoun_hint is not None:
                acc.pronoun_hint = eff.pronoun_hint
            if eff.jungian_hint is not None:
                acc.jungian_hint = eff.jungian_hint
            if eff.rpg_role_hint is not None:
                acc.rpg_role_hint = eff.rpg_role_hint
            # Phase 1 IOU — spaghetti_western chargen-choice reputation tag.
            if eff.reputation_bonus is not None:
                acc.reputation_bonus = eff.reputation_bonus

            # Multi-value accumulation — item_hints skips sentinel "none" /
            # empty strings to match the Rust filter.
            if eff.item_hint is not None and eff.item_hint not in ("", "none"):
                acc.item_hints.append(eff.item_hint)

            # Backstory fragment collection with pronoun-only filter.
            if result.choice_description is not None:
                is_pronoun_only = eff.pronoun_hint is not None and all(
                    v is None
                    for v in (
                        eff.class_hint,
                        eff.race_hint,
                        eff.mutation_hint,
                        eff.item_hint,
                        eff.affinity_hint,
                        eff.training_hint,
                        eff.background,
                        eff.personality_trait,
                        eff.emotional_state,
                        eff.relationship,
                        eff.goals,
                        eff.rig_type_hint,
                        eff.rig_trait,
                        eff.catch_phrase,
                    )
                )
                if not is_pronoun_only:
                    acc.backstory_fragments.append(result.choice_description)

            # Stat bonuses accumulate additively across all scenes.
            for stat, bonus in eff.stat_bonuses.items():
                acc.stat_bonuses[stat] = acc.stat_bonuses.get(stat, 0) + bonus

        return acc

    # --- Actions: scene walking ---

    def apply_choice(self, index: int) -> None:
        """Apply a numbered choice to the current scene.

        Raises WrongPhaseError if not in InProgress, InvalidChoiceError if
        index is out of range.

        Transitions: if the scene has a hook_prompt, moves to
        AwaitingFollowup; otherwise advances to the next scene (or
        Confirmation if this was the last scene).
        """
        match self._phase:
            case InProgress(scene_index=scene_index):
                pass
            case AwaitingFollowup():
                raise WrongPhaseError(expected="InProgress", actual="AwaitingFollowup")
            case Confirmation():
                raise WrongPhaseError(expected="InProgress", actual="Confirmation")
            case _:  # pragma: no cover
                raise AssertionError(f"unknown phase: {self._phase!r}")

        scene = self._scenes[scene_index]
        if index >= len(scene.choices):
            # Rust uses saturating_sub on max; we mirror for parity.
            max_index = max(len(scene.choices) - 1, 0)
            raise InvalidChoiceError(index=index, max_index=max_index)

        choice = scene.choices[index]
        effects = choice.mechanical_effects
        hooks = extract_hooks(scene.id, effects)
        anchors = extract_anchors(scene.id, effects)

        self._results.append(
            SceneResult(
                input_type=ChoiceInput(index=index),
                effects_applied=effects,
                hooks_added=hooks,
                anchors_added=anchors,
                choice_description=choice.description,
            )
        )

        if scene.hook_prompt is not None:
            self._phase = AwaitingFollowup(
                scene_index=scene_index,
                hook_prompt=scene.hook_prompt,
            )
        else:
            self._advance_scene(scene_index)

    def apply_freeform(self, text: str) -> None:
        """Apply freeform text input to the current scene.

        Allowed when `scene.allows_freeform` is True OR the scene has no
        choices (name-entry scenes at the end of chargen). Raises
        FreeformNotAllowedError otherwise.

        Uses scene-level `mechanical_effects` if present (e.g. name/stat
        scenes declaring stat_generation or equipment_generation). The
        actual stat roll re-execution based on scene directives lands
        in Slice 3; Slice 2 records the effects but does not re-roll.
        """
        if not isinstance(self._phase, InProgress):
            raise WrongPhaseError(expected="InProgress", actual=self._phase_name())
        scene_index = self._phase.scene_index
        scene = self._scenes[scene_index]

        # Allow freeform only when the scene explicitly allows it, OR when
        # the scene has no choices (name-entry scenes at the end of chargen).
        if not scene.allows_freeform and scene.choices:
            raise FreeformNotAllowedError()

        # Use scene-level mechanical_effects if present, otherwise empty.
        effects = (
            scene.mechanical_effects
            if scene.mechanical_effects is not None
            else MechanicalEffects()
        )

        # Slice 2: scene-level stat_generation directive is recorded via
        # effects_applied but the re-roll is deferred to Slice 3. Scene
        # walking does not re-execute stat generation — only construction-
        # time eager rolling does (Slice 3).

        hooks = extract_hooks(scene.id, effects)
        anchors = extract_anchors(scene.id, effects)

        self._results.append(
            SceneResult(
                input_type=FreeformInput(text=text),
                effects_applied=effects,
                hooks_added=hooks,
                anchors_added=anchors,
                choice_description=None,
            )
        )

        if scene.hook_prompt is not None:
            self._phase = AwaitingFollowup(
                scene_index=scene_index,
                hook_prompt=scene.hook_prompt,
            )
        else:
            self._advance_scene(scene_index)

    def answer_followup(self, text: str) -> None:
        """Answer a followup prompt while in AwaitingFollowup state.

        Inserts a Wound hook at position 0 of the most recent result — the
        followup answer is the player's primary hook (trauma description,
        motive elaboration, backstory beat). Advances to the next scene
        (or Confirmation).
        """
        if not isinstance(self._phase, AwaitingFollowup):
            raise WrongPhaseError(
                expected="AwaitingFollowup", actual=self._phase_name()
            )
        scene_index = self._phase.scene_index
        scene_id = self._scenes[scene_index].id

        # Insert the followup hook at position 0 on the most recent result.
        if self._results:
            self._results[-1].hooks_added.insert(
                0,
                NarrativeHook(
                    hook_type=HookType.WOUND,
                    source_scene=scene_id,
                    text=text,
                    mechanical_key=None,
                ),
            )

        self._advance_scene(scene_index)

    def apply_auto_advance(self) -> None:
        """Auto-advance a display-only scene (no choices, no freeform).

        For scenes that narrate and wait for the player's Continue ack.
        Applies scene-level mechanical_effects and advances. Raises
        InvalidChoiceError if the scene requires input.

        Slice 2 records the effects but does not re-execute stat rolling
        — that lands in Slice 3.
        """
        if not isinstance(self._phase, InProgress):
            raise WrongPhaseError(expected="InProgress", actual=self._phase_name())
        scene_index = self._phase.scene_index
        scene = self._scenes[scene_index]

        if scene.choices or scene.allows_freeform:
            raise InvalidChoiceError(index=0, max_index=len(scene.choices))

        effects = (
            scene.mechanical_effects
            if scene.mechanical_effects is not None
            else MechanicalEffects()
        )

        self._results.append(
            SceneResult(
                input_type=ChoiceInput(index=0),
                effects_applied=effects,
                hooks_added=[],
                anchors_added=[],
                choice_description=None,
            )
        )

        self._advance_scene(scene_index)

    def go_back(self) -> None:
        """Navigate backward, undoing the last scene result.

        Pops the most recent SceneResult and sets the phase back to that
        scene's index. Raises WrongPhaseError if there are no results to
        revert (we're at the first scene with no history).
        """
        if not self._results:
            raise WrongPhaseError(
                expected="InProgress with history",
                actual="no previous scenes to return to",
            )
        self._results.pop()
        target = len(self._results)
        self._phase = InProgress(scene_index=target)

    def go_to_scene(self, target: int) -> None:
        """Jump to a specific scene index, discarding results from that
        scene onward.

        Used by the "edit" action from the review/confirmation screen.
        Raises WrongPhaseError if target is out of range.
        """
        if target >= len(self._scenes):
            raise WrongPhaseError(
                expected=f"scene index < {len(self._scenes)}",
                actual=f"target scene index {target}",
            )
        self._results = self._results[:target]
        self._phase = InProgress(scene_index=target)

    def revert(self) -> None:
        """Revert the last scene — pop the SceneResult and go back one.

        Port of sidequest_game::builder::CharacterBuilder::revert. Distinct
        from go_back in that go_back's "at-the-first-scene" guard raises
        WrongPhaseError; revert raises CannotRevertError. The semantic
        difference is cosmetic in Rust but callers depend on the specific
        error variant — keep them distinct in Python too.
        """
        if not self._results:
            raise CannotRevertError()
        self._results.pop()
        self._phase = InProgress(scene_index=len(self._results))

    # --- Private helpers ---

    def _advance_scene(self, current: int) -> None:
        """Advance to the next scene, or transition to Confirmation if
        `current` was the last scene."""
        next_index = current + 1
        if next_index >= len(self._scenes):
            self._phase = CONFIRMATION
        else:
            self._phase = InProgress(scene_index=next_index)

    def _phase_name(self) -> str:
        """Human-readable phase name for error messages."""
        match self._phase:
            case InProgress():
                return "InProgress"
            case AwaitingFollowup():
                return "AwaitingFollowup"
            case Confirmation():
                return "Confirmation"
            case _:  # pragma: no cover
                return "Unknown"


__all__ = [
    # Hooks and anchors
    "HookType",
    "NarrativeHook",
    "LoreAnchor",
    "extract_hooks",
    "extract_anchors",
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
    # Builder
    "CharacterBuilder",
    # String helpers
    "humanize_snake_case",
    "strip_unmatched_placeholders",
]
