"""Game state composition — GameSnapshot, WorldStatePatch, NpcPatch.

Port of sidequest_game::state (state.rs, 919 LOC) — Phase 1 slice.

GameSnapshot composes all domain types (port lesson #4). Serializable for
persistence and WebSocket broadcast.

Phase 1 includes: all fields from GameSnapshot to avoid elision, with comments
marking which fields belong to deferred subsystems. Methods that depend on
deferred subsystems (apply_merchant_transactions, etc.) are ported as-is where
they don't pull in deferred types, or noted as deferred.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory, placeholder_edge_pool
from sidequest.game.turn import TurnManager


# ---------------------------------------------------------------------------
# NarrativeEntry — narrative log entries
# ---------------------------------------------------------------------------


class EncounterTag(BaseModel):
    """NPC encounter tag within a narrative entry (story F3).

    Port of sidequest_game::narrative::EncounterTag.
    """

    model_config = {"extra": "forbid"}

    npc_id: str
    encounter_type: str
    archetype_id: str | None = None
    notes: str | None = None


class NarrativeEntry(BaseModel):
    """A single narrative entry in the game log.

    Port of sidequest_game::narrative::NarrativeEntry.
    P1-required: narrator reads narrative_log for context.
    """

    model_config = {"extra": "forbid"}

    timestamp: int = 0
    round: int = 0
    author: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    encounter_tags: list[EncounterTag] = Field(default_factory=list)
    speaker: str | None = None
    entry_type: str | None = None


# ---------------------------------------------------------------------------
# NPC types (minimal — Npc is a deferred full port but needed for GameSnapshot)
# ---------------------------------------------------------------------------


class Npc(BaseModel):
    """Non-player character — minimal Phase 1 port.

    Port of sidequest_game::npc::Npc.
    Full port (OCEAN, BeliefState, ResolutionTier) is P5-deferred (scenario system).
    Fields are included to match the Rust struct for JSON round-tripping.
    """

    model_config = {"extra": "forbid"}

    # Flattened CreatureCore fields (Rust: #[serde(flatten)] pub core: CreatureCore)
    # Stored as nested in Python for clarity, flattened in persistence.
    core: CreatureCore

    # NPC-specific fields (P1-required: narrator uses name, personality, disposition)
    voice_id: int | None = None
    disposition: int = 0
    location: str | None = None
    pronouns: str | None = None
    appearance: str | None = None
    age: str | None = None
    build: str | None = None
    height: str | None = None
    distinguishing_features: list[str] = Field(default_factory=list)

    # P5-deferred: OCEAN personality (story 10-1, scenario system)
    ocean: dict | None = None
    # P5-deferred: BeliefState (Epic 7, scenario system)
    belief_state: dict = Field(default_factory=dict)
    # P2-deferred: ResolutionTier (NPC enrichment system)
    resolution_tier: str = "spawn"
    non_transactional_interactions: int = 0
    # P2-deferred: archetype resolution fields
    jungian_id: str | None = None
    rpg_role_id: str | None = None
    npc_role_id: str | None = None
    resolved_archetype: str | None = None

    def name(self) -> str:
        return self.core.name


class NpcRegistryEntry(BaseModel):
    """Lightweight NPC registry entry for narrator prompt consistency.

    Port of sidequest_game::npc::NpcRegistryEntry.
    P1-required: narrator uses registry for name/identity consistency.
    """

    model_config = {"extra": "forbid"}

    name: str
    role: str | None = None
    pronouns: str | None = None
    appearance: str | None = None
    last_seen_location: str | None = None
    last_seen_turn: int = 0


# ---------------------------------------------------------------------------
# NpcPatch — used in WorldStatePatch.npcs_present
# ---------------------------------------------------------------------------


class NpcPatch(BaseModel):
    """Patch for NPC upsert — used in npcs_present.

    Port of sidequest_game::state::NpcPatch.
    """

    model_config = {"extra": "forbid"}

    name: str
    description: str | None = None
    personality: str | None = None
    role: str | None = None
    pronouns: str | None = None
    appearance: str | None = None
    age: str | None = None
    build: str | None = None
    height: str | None = None
    distinguishing_features: list[str] | None = None
    location: str | None = None

    @field_validator("name")
    @classmethod
    def name_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be blank")
        return v


# ---------------------------------------------------------------------------
# WorldStatePatch
# ---------------------------------------------------------------------------


class DiscoveredFact(BaseModel):
    """A fact discovered by a character this turn (story 9-3).

    Port of sidequest_game::known_fact::DiscoveredFact (inline for Phase 1).
    P1-required: narrator-delivered facts routed to character known_facts.
    """

    model_config = {"extra": "forbid"}

    character_name: str
    fact: dict  # KnownFact as dict — avoid circular import


class WorldStatePatch(BaseModel):
    """Patch for world-level state (location, atmosphere, quests, regions).

    Port of sidequest_game::state::WorldStatePatch.
    Only Some fields are applied; None means "no change."

    P1-required: all fields ported. Used by narrator agent to update state.
    """

    model_config = {"extra": "forbid"}

    location: str | None = None
    time_of_day: str | None = None
    atmosphere: str | None = None
    quest_log: dict[str, str] | None = None
    quest_updates: dict[str, str] | None = None
    notes: list[str] | None = None
    current_region: str | None = None
    discovered_regions: list[str] | None = None
    discovered_routes: list[str] | None = None
    discover_regions: list[str] | None = None
    discover_routes: list[str] | None = None
    hp_changes: dict[str, int] | None = None
    npc_attitudes: dict[str, int] | None = None
    npcs_present: list[NpcPatch] | None = None
    active_stakes: str | None = None
    lore_established: list[str] | None = None
    discovered_facts: list[DiscoveredFact] | None = None


# ---------------------------------------------------------------------------
# Minimal deferred-subsystem stubs needed as field types in GameSnapshot
# (no logic, only data containers for JSON round-tripping)
# ---------------------------------------------------------------------------


class TropeState(BaseModel):
    """Active trope state (minimal, for JSON round-tripping).

    Port of sidequest_game::trope::TropeState — P2-deferred full port.
    """

    model_config = {"extra": "ignore"}

    id: str = ""
    status: str = "dormant"
    progress: float = 0.0
    beats_fired: int = 0


class HistoryChapter(BaseModel):
    """Campaign history chapter (minimal, for JSON round-tripping).

    Port of sidequest_game::world_materialization::HistoryChapter — P3-deferred.
    """

    model_config = {"extra": "ignore"}

    title: str = ""
    summary: str = ""


class GenieWish(BaseModel):
    """Genie wish entry — power-grab with ironic consequences (F9).

    Port of sidequest_game::consequence::GenieWish — P5-deferred.
    """

    model_config = {"extra": "ignore"}

    wish_text: str = ""
    consequence: str = ""
    status: str = "pending"


class AxisValue(BaseModel):
    """Narrative axis value for /tone command (F2/F10).

    Port of sidequest_game::axis::AxisValue — P2-deferred.
    """

    model_config = {"extra": "ignore"}

    axis_id: str = ""
    value: float = 0.0


class AchievementTracker(BaseModel):
    """Achievement tracker (F7) — P6-deferred.

    Port of sidequest_game::achievement::AchievementTracker.
    """

    model_config = {"extra": "ignore"}

    achievements: list[dict] = Field(default_factory=list)


class ResourcePool(BaseModel):
    """Named resource pool with thresholds (story 16-10) — P4-deferred.

    Port of sidequest_game::resource_pool::ResourcePool.
    """

    model_config = {"extra": "ignore"}

    name: str = ""
    label: str = ""
    current: float = 0.0
    min: float = 0.0
    max: float = 100.0
    voluntary: bool = False
    decay_per_turn: float = 0.0
    thresholds: list[dict] = Field(default_factory=list)


# ScenarioState is fully deferred (P5 — Epic 7 / scenario system)
# We use dict | None for the field type to avoid pulling in unported types.


# ---------------------------------------------------------------------------
# GameSnapshot — the complete game state at a point in time
# ---------------------------------------------------------------------------


class GameSnapshot(BaseModel):
    """The complete game state at a point in time.

    Port of sidequest_game::state::GameSnapshot (state.rs, 919 LOC).

    All fields ported to match the Rust JSON schema for save compatibility.
    Deferred-subsystem fields are present but noted:
    - encounter: P3-deferred (StructuredEncounter / combat engine)
    - active_tropes: P2-deferred (trope engine)
    - campaign_maturity / world_history: P3-deferred (world materialization)
    - genie_wishes: P5-deferred (consequence engine)
    - axis_values: P2-deferred (tone system)
    - achievement_tracker: P6-deferred
    - scenario_state: P5-deferred (Epic 7)
    - discovered_rooms: P3-deferred (room-graph navigation)
    - resources: P4-deferred (resource pools)

    P1-required: genre_slug, world_slug, characters, npcs, location,
                 time_of_day, quest_log, notes, narrative_log, atmosphere,
                 current_region, discovered_regions, discovered_routes,
                 turn_manager, active_stakes, lore_established,
                 npc_registry, player_dead.
    """

    model_config = {"extra": "ignore"}  # forward-compat: ignore unknown save fields

    # Session identity (P1-required)
    genre_slug: str = ""
    world_slug: str = ""

    # Core game entities (P1-required)
    characters: list[Character] = Field(default_factory=list)
    npcs: list[Npc] = Field(default_factory=list)

    # World state (P1-required)
    location: str = ""
    time_of_day: str = ""
    quest_log: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    narrative_log: list[NarrativeEntry] = Field(default_factory=list)

    # P3-deferred: StructuredEncounter (ADR-033 confrontation engine)
    encounter: dict | None = None

    # P2-deferred: trope engine state
    active_tropes: list[TropeState] = Field(default_factory=list)

    # World descriptors (P1-required)
    atmosphere: str = ""
    current_region: str = ""
    discovered_regions: list[str] = Field(default_factory=list)
    discovered_routes: list[str] = Field(default_factory=list)

    # Turn tracking (P1-required)
    turn_manager: TurnManager = Field(default_factory=TurnManager)

    # Session metadata
    last_saved_at: datetime | None = None

    # Narrative state (P1-required)
    active_stakes: str = ""
    lore_established: list[str] = Field(default_factory=list)

    # Trope/pacing counters (P2-deferred: trope engagement multiplier)
    turns_since_meaningful: int = 0
    total_beats_fired: int = 0

    # P3-deferred: world materialization (campaign maturity)
    campaign_maturity: str = "Fresh"
    world_history: list[HistoryChapter] = Field(default_factory=list)

    # NPC registry (P1-required: narrator uses for name consistency)
    npc_registry: list[NpcRegistryEntry] = Field(default_factory=list)

    # P5-deferred: genie wishes (consequence engine, F9)
    genie_wishes: list[GenieWish] = Field(default_factory=list)

    # P2-deferred: narrative axis values (tone system, F2/F10)
    axis_values: list[AxisValue] = Field(default_factory=list)

    # P6-deferred: achievement tracker (F7)
    achievement_tracker: AchievementTracker = Field(default_factory=AchievementTracker)

    # P5-deferred: scenario state (Epic 7 — whodunit, belief state, clues)
    scenario_state: dict | None = None

    # P3-deferred: room-graph navigation (story 19-2)
    discovered_rooms: list[str] = Field(default_factory=list)

    # Combat state (P1-required: permadeath / death detection)
    player_dead: bool = False

    # P4-deferred: named resource pools (story 16-10)
    resources: dict[str, ResourcePool] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # State mutation methods
    # ------------------------------------------------------------------

    def apply_world_patch(self, patch: WorldStatePatch) -> None:
        """Apply a world state patch. Only Some fields are updated.

        Port of sidequest_game::state::GameSnapshot::apply_world_patch.
        """
        if patch.location is not None:
            self.location = patch.location
        if patch.time_of_day is not None:
            self.time_of_day = patch.time_of_day
        if patch.atmosphere is not None:
            self.atmosphere = patch.atmosphere
        if patch.quest_log is not None:
            self.quest_log = patch.quest_log
        if patch.quest_updates is not None:
            self.quest_log.update(patch.quest_updates)
        if patch.notes is not None:
            self.notes = patch.notes
        if patch.current_region is not None:
            self.current_region = patch.current_region
        if patch.discovered_regions is not None:
            self.discovered_regions = patch.discovered_regions
        if patch.discovered_routes is not None:
            self.discovered_routes = patch.discovered_routes
        if patch.discover_regions is not None:
            for r in patch.discover_regions:
                if r not in self.discovered_regions:
                    self.discovered_regions.append(r)
        if patch.discover_routes is not None:
            for r in patch.discover_routes:
                if r not in self.discovered_routes:
                    self.discovered_routes.append(r)
        if patch.active_stakes is not None:
            self.active_stakes = patch.active_stakes
        if patch.lore_established is not None:
            self.lore_established.extend(patch.lore_established)
        if patch.discovered_facts is not None:
            from sidequest.game.character import KnownFact
            for df in patch.discovered_facts:
                for ch in self.characters:
                    if ch.core.name == df.character_name:
                        kf = KnownFact.model_validate(df.fact)
                        ch.known_facts.append(kf)
        if patch.hp_changes is not None:
            for name, delta in patch.hp_changes.items():
                self._apply_hp_change(name, delta)
        if patch.npc_attitudes is not None:
            for name, delta in patch.npc_attitudes.items():
                for npc in self.npcs:
                    if npc.core.name == name:
                        npc.disposition = max(-100, min(100, npc.disposition + delta))
        if patch.npcs_present is not None:
            for npc_patch in patch.npcs_present:
                existing = next(
                    (n for n in self.npcs if n.core.name == npc_patch.name), None
                )
                if existing is not None:
                    self._merge_npc_patch(existing, npc_patch)
                else:
                    self.npcs.append(self._npc_from_patch(npc_patch))

    def _apply_hp_change(self, name: str, delta: int) -> None:
        for ch in self.characters:
            if ch.core.name == name:
                ch.core.apply_edge_delta(delta)
                return
        for npc in self.npcs:
            if npc.core.name == name:
                npc.core.apply_edge_delta(delta)
                return

    def _merge_npc_patch(self, npc: Npc, patch: NpcPatch) -> None:
        if patch.description is not None:
            npc.core.description = patch.description
        if patch.personality is not None:
            npc.core.personality = patch.personality
        if patch.pronouns is not None:
            npc.pronouns = patch.pronouns
        if patch.appearance is not None:
            npc.appearance = patch.appearance
        if patch.age is not None:
            npc.age = patch.age
        if patch.build is not None:
            npc.build = patch.build
        if patch.height is not None:
            npc.height = patch.height
        if patch.distinguishing_features is not None:
            npc.distinguishing_features = patch.distinguishing_features
        if patch.location is not None:
            npc.location = patch.location

    def _npc_from_patch(self, patch: NpcPatch) -> Npc:
        core = CreatureCore(
            name=patch.name,
            description=patch.description or "No description",
            personality=patch.personality or "Unknown",
            level=1,
            xp=0,
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
        )
        return Npc(
            core=core,
            pronouns=patch.pronouns,
            appearance=patch.appearance,
            age=patch.age,
            build=patch.build,
            height=patch.height,
            distinguishing_features=patch.distinguishing_features or [],
            location=patch.location,
        )

    def lowest_friendly_hp_ratio(self) -> float:
        """Lowest edge fraction among friendly characters. Returns 1.0 if none."""
        fracs = [ch.edge_fraction() for ch in self.characters if ch.is_friendly]
        return min(fracs) if fracs else 1.0
