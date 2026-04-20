"""Scenario pack types from scenarios/*/.

Port of sidequest-genre/src/models/scenario.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleHook(BaseModel):
    """A required hook for a player role."""

    model_config = {"extra": "forbid"}

    hook_type: str = Field(alias="type", serialization_alias="type")
    prompt: str

    model_config = {"extra": "forbid", "populate_by_name": True}


class PlayerRole(BaseModel):
    """A player role within a scenario."""

    model_config = {"extra": "forbid"}

    id: str
    archetype_hint: str
    narrative_position: str
    required_hooks: list[RoleHook] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    suggested_flavors: list[str] = Field(default_factory=list)


class Act(BaseModel):
    """An act within a scenario's pacing structure."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    scenes: int
    trope_range: list[float]
    narrator_tone: str


class PressureEvent(BaseModel):
    """A pressure event triggered at a specific scene."""

    model_config = {"extra": "forbid"}

    at_scene: int
    event: str


class EscalationBeat(BaseModel):
    """An escalation beat at a progression threshold."""

    model_config = {"extra": "forbid"}

    at: float
    inject: str


class Pacing(BaseModel):
    """Scenario pacing and act structure."""

    model_config = {"extra": "forbid"}

    scene_budget: int
    acts: list[Act] = Field(default_factory=list)
    pressure_events: list[PressureEvent] = Field(default_factory=list)
    escalation_beats: list[EscalationBeat] = Field(default_factory=list)


class Suspect(BaseModel):
    """A suspect in the assignment matrix."""

    model_config = {"extra": "forbid"}

    id: str
    archetype_ref: str
    can_be_guilty: bool
    motives: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)


class AssignmentMatrix(BaseModel):
    """Suspect/motive/method assignment matrix."""

    model_config = {"extra": "forbid"}

    suspects: list[Suspect] = Field(default_factory=list)
    motives: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)


class ClueNode(BaseModel):
    """A single clue node in the graph."""

    model_config = {"extra": "forbid"}

    id: str
    clue_type: str = Field(alias="type", serialization_alias="type")
    description: str
    discovery_method: str
    visibility: str
    locations: list[str] = Field(default_factory=list)
    implicates: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    red_herring: bool = False

    model_config = {"extra": "forbid", "populate_by_name": True}


class ClueGraph(BaseModel):
    """Clue dependency graph."""

    model_config = {"extra": "forbid"}

    nodes: list[ClueNode] = Field(default_factory=list)


class AtmosphereVariant(BaseModel):
    """A single atmosphere variant."""

    model_config = {"extra": "forbid"}

    id: str
    weather: str
    setting_status: str
    mood_baseline: str
    concurrent_event: str | None = None
    npc_mood_overrides: dict[str, str] = Field(default_factory=dict)


class AtmosphereMatrix(BaseModel):
    """Atmosphere variant matrix."""

    model_config = {"extra": "forbid"}

    variants: list[AtmosphereVariant] = Field(default_factory=list)


class Suspicion(BaseModel):
    """A suspicion one NPC has about another."""

    model_config = {"extra": "forbid"}

    target: str
    confidence: float
    basis: str


class InitialBeliefs(BaseModel):
    """An NPC's initial beliefs and suspicions."""

    model_config = {"extra": "forbid"}

    facts: list[str] = Field(default_factory=list)
    suspicions: list[Suspicion] = Field(default_factory=list)


class WhenGuilty(BaseModel):
    """NPC behavior when guilty."""

    model_config = {"extra": "forbid"}

    truth: str
    cover_story: str
    breaking_evidence: list[str] = Field(default_factory=list)


class WhenInnocent(BaseModel):
    """NPC behavior when innocent."""

    model_config = {"extra": "forbid"}

    actual_activity: str
    suspicion: str = ""
    secret: str = ""


class ScenarioNpc(BaseModel):
    """An NPC within a scenario with branching behavior."""

    model_config = {"extra": "forbid"}

    id: str
    archetype_ref: str
    name: str
    initial_beliefs: InitialBeliefs
    when_guilty: WhenGuilty
    when_innocent: WhenInnocent


class ScenarioPack(BaseModel):
    """A scenario pack — assembled from scenario.yaml + supporting files."""

    model_config = {"extra": "forbid"}

    name: str
    version: str
    description: str
    duration_minutes: int
    max_players: int
    player_roles: list[PlayerRole] = Field(default_factory=list)
    pacing: Pacing
    assignment_matrix: AssignmentMatrix = Field(default_factory=AssignmentMatrix)
    clue_graph: ClueGraph = Field(default_factory=ClueGraph)
    atmosphere_matrix: AtmosphereMatrix = Field(default_factory=AtmosphereMatrix)
    npcs: list[ScenarioNpc] = Field(default_factory=list)
    allows_split_party: bool = False
