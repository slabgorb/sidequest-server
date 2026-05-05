"""GenrePack aggregate root and PackMeta.

Port of sidequest-genre/src/models/pack.rs.

Note: In Rust, GenrePack is assembled by the loader from multiple YAML files.
In Python, we represent it as a structured aggregate that can be built
by the loader (Story 41-3). The individual components are validated models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sidequest.game.projection.rules import ProjectionRules
from sidequest.genre.models.archetype_axes import BaseArchetypes
from sidequest.genre.models.archetype_constraints import ArchetypeConstraints
from sidequest.genre.models.archetype_funnels import ArchetypeFunnels
from sidequest.genre.models.audio import AudioConfig, VoicePresets
from sidequest.genre.models.authored_npc import AuthoredNpc
from sidequest.genre.models.axes import AxesConfig
from sidequest.genre.models.character import (
    BackstoryTables,
    CharCreationScene,
    EquipmentTables,
    NpcArchetype,
    VisualStyle,
)
from sidequest.genre.models.chassis import ChassisClassesConfig
from sidequest.genre.models.culture import Culture
from sidequest.genre.models.inventory import InventoryConfig
from sidequest.genre.models.legends import Legend
from sidequest.genre.models.lethality import LethalityPolicy
from sidequest.genre.models.lore import Lore, WorldLore
from sidequest.genre.models.narrative import (
    Achievement,
    BeatVocabulary,
    Opening,
    PowerTier,
    Prompts,
)
from sidequest.genre.models.npc_traits import NpcTraitsDatabase
from sidequest.genre.models.ocean import DramaThresholds
from sidequest.genre.models.progression import ProgressionConfig
from sidequest.genre.models.rigs_world import ChassisInstanceConfig
from sidequest.genre.models.rules import RulesConfig
from sidequest.genre.models.scenario import ScenarioPack
from sidequest.genre.models.theme import GenreTheme
from sidequest.genre.models.tropes import TropeDefinition
from sidequest.genre.models.visibility import VisibilityBaseline
from sidequest.genre.models.world import CartographyConfig, WorldConfig


class RecommendedPlayers(BaseModel):
    """Recommended player count for a genre pack."""

    model_config = {"extra": "forbid"}

    min: int
    max: int
    sweet_spot: int | None = None


class Inspiration(BaseModel):
    """A creative inspiration reference."""

    model_config = {"extra": "forbid"}

    name: str
    element: str


class PackMeta(BaseModel):
    """Genre pack metadata from pack.yaml."""

    model_config = {"extra": "forbid"}

    name: str
    version: str
    description: str
    min_sidequest_version: str
    refine_hooks: bool | None = None
    inspirations: list[Inspiration] = Field(default_factory=list)
    era_range: str | None = None
    core_vibe: str | None = None
    emotional_tone: list[str] = Field(default_factory=list)
    differentiation: str | None = None
    lobby_blurb: str | None = None
    recommended_players: RecommendedPlayers | None = None


class PortraitManifestEntry(BaseModel):
    """A character entry in a portrait manifest.

    ``extra="ignore"`` matches Rust parity: the Rust struct doesn't use
    ``#[serde(deny_unknown_fields)]``, so packs can author flavor fields
    the engine doesn't consume (dress_1878, register, flux_prompt,
    negative_additions, references — all present on ``the_real_mccoy``).
    The Rust loader drops them silently; we match that rather than failing
    the whole pack load.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    name: str
    role: str = ""
    character_type: str = Field(default="", alias="type", serialization_alias="type")
    appearance: str = ""
    culture_aesthetic: str = ""
    element_visual: str = ""


class DungeonConfig(BaseModel):
    """Top of dungeons/<slug>/dungeon.yaml — slim variant of WorldConfig.

    A dungeon is a delvable child of a hub world. The hub owns regional
    lore, factions, archetype roster, audio, and the hamlet; each
    dungeon owns its own cartography, openings, rooms, creatures,
    encounter tables, drift profile, wound profile, and approach hamlet.

    `parent_world` MUST equal the slug of the world directory containing
    `dungeons/`. The loader enforces this; mismatches are loud authoring
    errors per the No Silent Fallbacks rule.
    """

    # extra=allow so dungeons can carry the same flatten-extras as WorldConfig
    # (keeper, tagline, sin, etc.) without each one being a model field.
    model_config = {"extra": "allow"}

    parent_world: str
    name: str
    description: str
    sin: str | None = None
    cover_poi: str | None = None
    axis_snapshot: dict[str, float] = Field(default_factory=dict)


class Dungeon(BaseModel):
    """A delvable dungeon under a hub world's `dungeons/` subdirectory.

    Carries everything required to run a delve: cartography, openings,
    legends, tropes, plus narrator-zone fodder (drift_profile,
    wound_profile) and a per-dungeon approach hamlet. Most per-dungeon
    files (creatures, encounter_tables, factions, rooms) are kept as
    raw YAML for now and promoted to typed models when consumers exist.
    """

    model_config = {"extra": "allow"}

    config: DungeonConfig
    cartography: CartographyConfig
    openings: list[Opening] = Field(default_factory=list)
    legends: list[Legend] = Field(default_factory=list)
    tropes: list[TropeDefinition] = Field(default_factory=list)
    visual_style: Any = None
    portrait_manifest: list[PortraitManifestEntry] = Field(default_factory=list)
    # Narrator-zone fodder; engine consumes in a later plan.
    drift_profile: Any = None
    wound_profile: Any = None
    # Approach hamlet (Ashgate / Copperbridge / Gristwell).
    approach: Any = None
    # Raw passthrough — schema upgrades when consumers exist.
    factions_raw: Any = None
    creatures_raw: Any = None
    encounter_tables_raw: Any = None
    rooms_raw: Any = None


class World(BaseModel):
    """A world within a genre pack, assembled from worlds/{slug}/.

    Fields are populated by the loader (Story 41-3).

    A *leaf* world owns its own cartography and openings — this is the
    classic shape and what every world other than `caverns_three_sins`
    looks like today.

    A *hub* world has `dungeons/` populated, no world-level cartography,
    and no world-level openings. The session handler refuses to start a
    delve in a hub world until the dungeon-pick UI ships
    (engine-plan item 4 of the Hamlet-of-Sünden spec).

    Invariant: `cartography is None` ⇔ `dungeons` is non-empty.
    """

    # No extra="forbid" at aggregate level — loader populates this
    model_config = {"extra": "allow"}

    config: WorldConfig
    lore: WorldLore
    legends: list[Legend] = Field(default_factory=list)
    # None on hub worlds; required on leaf worlds. Enforced by the loader.
    cartography: CartographyConfig | None = None
    cultures: list[Culture] = Field(default_factory=list)
    tropes: list[TropeDefinition] = Field(default_factory=list)
    archetypes: list[NpcArchetype] = Field(default_factory=list)
    visual_style: Any = None  # can be VisualStyle or richer world-level JSON
    history: Any = None
    legends_raw: Any = None
    portrait_manifest: list[PortraitManifestEntry] = Field(default_factory=list)
    archetype_funnels: ArchetypeFunnels | None = None
    openings: list[Opening] = Field(default_factory=list)
    authored_npcs: list[AuthoredNpc] = Field(default_factory=list)
    char_creation: list[CharCreationScene] = Field(default_factory=list)
    chassis_instances: list[ChassisInstanceConfig] = Field(default_factory=list)
    magic_register: str = ""
    # Hub-world children. Empty for leaf worlds.
    dungeons: dict[str, Dungeon] = Field(default_factory=dict)
    # Hamlet-of-Sünden hub data; raw YAML for now (typed schema in a later plan).
    hamlet: Any = None


class GenrePack(BaseModel):
    """A fully-loaded genre pack with all YAML files assembled.

    This is an aggregate root built by the loader (Story 41-3).
    Each field corresponds to one (or more) YAML files in the pack directory.
    """

    model_config = {"extra": "allow"}

    meta: PackMeta
    rules: RulesConfig
    lore: Lore
    theme: GenreTheme
    archetypes: list[NpcArchetype] = Field(default_factory=list)
    char_creation: list[CharCreationScene] = Field(default_factory=list)
    visual_style: VisualStyle
    progression: ProgressionConfig
    axes: AxesConfig
    audio: AudioConfig
    cultures: list[Culture] = Field(default_factory=list)
    prompts: Prompts
    tropes: list[TropeDefinition] = Field(default_factory=list)
    beat_vocabulary: BeatVocabulary | None = None
    chassis_classes: ChassisClassesConfig | None = None
    achievements: list[Achievement] = Field(default_factory=list)
    voice_presets: VoicePresets | None = None
    power_tiers: dict[str, list[PowerTier]] = Field(default_factory=dict)
    worlds: dict[str, World] = Field(default_factory=dict)
    scenarios: dict[str, ScenarioPack] = Field(default_factory=dict)
    drama_thresholds: DramaThresholds | None = None
    inventory: InventoryConfig | None = None
    openings: list[Opening] = Field(default_factory=list)
    backstory_tables: BackstoryTables | None = None
    equipment_tables: EquipmentTables | None = None
    base_archetypes: BaseArchetypes | None = None
    archetype_constraints: ArchetypeConstraints | None = None
    npc_traits: NpcTraitsDatabase | None = None
    projection_rules: ProjectionRules | None = None
    visibility_baseline: VisibilityBaseline | None = None
    lethality_policy: LethalityPolicy | None = None
    source_dir: Path | None = None

    # Convenience accessor for pack name
    @property
    def name(self) -> str:
        return self.meta.name
