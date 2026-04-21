"""Character — unified model combining narrative identity and mechanical stats.

Port of sidequest_game::character (character.rs, 314 LOC).
ADR-007: Single struct, narrative-first field ordering.

Phase 1 slice — narrator-relevant fields fully ported. Combat trait methods
(is_broken, edge_fraction) are ported as Python methods since the Combatant
trait is an internal concern. Fields from deferred subsystems are included
(no elision) with comments marking their Phase.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from sidequest.game.ability import AbilitySource
from sidequest.game.creature_core import CreatureCore


class AbilityDefinition(BaseModel):
    """Dual-voice ability representation.

    Port of sidequest_game::ability::AbilityDefinition.
    genre_description: player-facing narrative description.
    mechanical_effect: engine-facing trigger text.
    involuntary: if True, narrator can trigger without player choice.
    source: how the character acquired this ability (Race/Class/Item/Play).
    """

    model_config = {"extra": "forbid"}

    name: str
    genre_description: str
    mechanical_effect: str
    involuntary: bool = False
    source: AbilitySource


class KnownFact(BaseModel):
    """A fact the character has learned — accumulates monotonically.

    Port of sidequest_game::known_fact::KnownFact.
    P1-required: narrator uses known_facts for context.
    """

    model_config = {"extra": "forbid"}

    content: str
    confidence: str = "confirmed"
    # P5-deferred: source/learned_turn used by scenario system
    source: str = "GameEvent"
    learned_turn: int = 0


class AffinityState(BaseModel):
    """Per-affinity tier tracking for ability progression.

    Port of sidequest_game::affinity::AffinityState.
    P6-deferred: advancement/affinity progression, not needed for narration.
    """

    model_config = {"extra": "forbid"}

    affinity_id: str
    tier: int = 0
    progress: float = 0.0


class Character(BaseModel):
    """A player character with unified narrative + mechanical identity.

    Port of sidequest_game::character::Character.

    Rust uses #[serde(flatten)] for core: CreatureCore — all CreatureCore
    fields appear at the top level in JSON, not nested under "core".
    Python replicates this by embedding CreatureCore fields directly OR
    nesting with model serialization that flattens. We nest under `core`
    internally but expose the flattened JSON schema via model_serializer
    in session.py round-trips.

    For narrator prompt building, access character.core.name, etc.

    P1-required fields: core, backstory, narrative_state, hooks, char_class,
                        race, pronouns, stats, abilities, known_facts,
                        is_friendly.
    P6-deferred fields: affinities (affinity progression).
    P2-deferred fields: resolved_archetype, archetype_provenance (chargen axis).
    """

    model_config = {"extra": "forbid"}

    # Embedded CreatureCore (flattened in Rust JSON via #[serde(flatten)])
    # In Python we keep it nested — callers use character.core.name, etc.
    core: CreatureCore

    # Narrative identity (P1-required)
    backstory: str
    narrative_state: str = ""
    hooks: list[str] = Field(default_factory=list)

    # Mechanical identity (P1-required)
    char_class: str
    race: str
    pronouns: str = ""
    stats: dict[str, int] = Field(default_factory=dict)

    # Abilities (P1-required — narrator uses genre_description for context)
    abilities: list[AbilityDefinition] = Field(default_factory=list)

    # Character knowledge (P1-required — narrator uses known_facts for continuity)
    known_facts: list[KnownFact] = Field(default_factory=list)

    # P6-deferred: affinity tier progression (advancement system, Epic F8)
    affinities: list[AffinityState] = Field(default_factory=list)

    # P1-required: determines narrator behaviour (player vs enemy framing)
    is_friendly: bool = True

    # P2-deferred: archetype resolution (chargen axis system, story G2)
    resolved_archetype: str | None = None
    archetype_provenance: dict | None = None

    @field_validator("backstory")
    @classmethod
    def backstory_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("backstory cannot be blank")
        return v

    @field_validator("char_class")
    @classmethod
    def char_class_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("char_class cannot be blank")
        return v

    @field_validator("race")
    @classmethod
    def race_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("race cannot be blank")
        return v

    # ------------------------------------------------------------------
    # Combatant-equivalent methods (Rust: impl Combatant for Character)
    # ------------------------------------------------------------------

    def name(self) -> str:
        """Character display name."""
        return self.core.name

    def edge(self) -> int:
        """Current composure (edge) value."""
        return self.core.edge.current

    def max_edge(self) -> int:
        """Maximum composure value."""
        return self.core.edge.max

    def level(self) -> int:
        """Character level."""
        return self.core.level

    def is_broken(self) -> bool:
        """True when edge is at or below zero (combatant is down).

        Port of Rust ``Combatant::is_broken`` default: ``self.edge() <= 0``.
        Negative edge counts as broken; ``== 0`` would drift from Rust.
        Fixed in story 42-1 (see session Delivery Findings).
        """
        return self.core.edge.current <= 0

    def edge_fraction(self) -> float:
        """Edge fraction as float in [0.0, 1.0].

        Port of Rust ``Combatant::edge_fraction`` default: returns ``0.0``
        when ``max_edge == 0`` (NOT ``1.0``, NOT ``ZeroDivisionError``).
        Fixed in story 42-1 (see session Delivery Findings).
        """
        if self.core.edge.max == 0:
            return 0.0
        return self.core.edge.current / self.core.edge.max
