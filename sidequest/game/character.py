"""Character — unified model combining narrative identity and mechanical stats.

ADR-007: Single struct, narrative-first field ordering.

Phase 1 slice — narrator-relevant fields fully covered. Combat methods
(``is_broken``, ``edge_fraction``) live as Python methods since the
``Combatant`` Protocol is an internal concern. Fields from deferred
subsystems are included (no elision) with comments marking their phase.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from sidequest.game.ability import AbilitySource
from sidequest.game.creature_core import CreatureCore


class AbilityDefinition(BaseModel):
    """Dual-voice ability representation.

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

    P6-deferred: advancement/affinity progression, not needed for
    narration.
    """

    model_config = {"extra": "forbid"}

    affinity_id: str
    tier: int = 0
    progress: float = 0.0


class Character(BaseModel):
    """A player character with unified narrative + mechanical identity.

    CreatureCore is nested under ``core`` internally — callers access
    fields via ``character.core.name`` etc. For narrator prompt building,
    access ``character.core.name``.

    P1-required fields: core, backstory, narrative_state, hooks, char_class,
                        race, pronouns, stats, abilities, known_facts,
                        is_friendly.
    P6-deferred fields: affinities (affinity progression).
    P2-deferred fields: resolved_archetype, archetype_provenance (chargen axis).
    """

    model_config = {"extra": "forbid"}

    # Embedded CreatureCore — callers use character.core.name, etc.
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

    # Chargen-derived narrative identity (canned-openings P2 — used by
    # _populate_opening_directive_on_chargen_complete to filter Openings
    # by triggers.backgrounds and to render PC name forms in the
    # chassis-voice block).
    background: str = ""
    drive: str = ""
    first_name: str = ""
    last_name: str = ""
    nickname: str = ""

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
    # Combatant Protocol implementation
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

        ``self.edge() <= 0`` — negative edge counts as broken.
        """
        return self.core.edge.current <= 0

    def edge_fraction(self) -> float:
        """Edge fraction as float in [0.0, 1.0].

        Returns ``0.0`` when ``max_edge == 0`` (NOT ``1.0``, NOT
        ``ZeroDivisionError``).
        """
        if self.core.edge.max == 0:
            return 0.0
        return self.core.edge.current / self.core.edge.max
