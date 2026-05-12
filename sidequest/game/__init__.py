"""sidequest.game — Phase 1 minimal slice of the game engine.

Phase 1 exports:
- Character, CreatureCore, EdgePool, EdgeThreshold, Inventory
- GameSnapshot, WorldStatePatch, NpcPatch, NpcPoolMember, NarrativeEntry
- StateDelta (game-layer), StateSnapshot, snapshot, compute_delta
- TurnManager, TurnPhase
- CommandHandler, CommandResult, BUILTIN_COMMANDS
- SqliteStore, SavedSession, SessionMeta, PersistError
- Resource pools (ADR-033): ResourcePool, ResourceThreshold,
  ResourcePatch, ResourcePatchOp, ResourcePatchResult, ResourcePatchError,
  UnknownResource, NotVoluntary, detect_crossings, mint_threshold_lore
- Encounter: StructuredEncounter,
  EncounterActor, EncounterMetric, EncounterPhase,
  RigType, SecondaryStats, StatValue

Phase 2+ (combat, dice, advancement) are deferred — not exported here.
"""

from sidequest.game.character import (
    AbilityDefinition,
    AffinityState,
    Character,
    KnownFact,
)
from sidequest.game.combatant import Combatant
from sidequest.game.commands import (
    BUILTIN_COMMANDS,
    CommandHandler,
    CommandResult,
    DisplayResult,
    ErrorResult,
    GmCommand,
    InventoryCommand,
    MapCommand,
    QuestsCommand,
    SaveCommand,
    StateMutationResult,
    StatusCommand,
)
from sidequest.game.creature_core import (
    PLACEHOLDER_EDGE_BASE_MAX,
    CreatureCore,
    EdgePool,
    EdgeThreshold,
    Inventory,
    RecoveryTrigger,
    placeholder_edge_pool,
)
from sidequest.game.delta import (
    StateDelta,
    StateSnapshot,
    compute_delta,
    snapshot,
)
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    RigType,
    SecondaryStats,
    StatValue,
    StructuredEncounter,
)
from sidequest.game.monster_manual import (
    EntryState,
    ManualEncounter,
    ManualNpc,
    MonsterManual,
)
from sidequest.game.npc_pool import NpcPoolMember
from sidequest.game.persistence import (
    DatabaseError,
    NotFoundError,
    PersistError,
    SavedSession,
    SerializationError,
    SessionMeta,
    SqliteStore,
)
from sidequest.game.resource_pool import (
    NotVoluntary,
    ResourcePatch,
    ResourcePatchError,
    ResourcePatchOp,
    ResourcePatchResult,
    ResourcePool,
    ResourceThreshold,
    UnknownResource,
    mint_threshold_lore,
)
from sidequest.game.session import (
    AchievementTracker,
    AxisValue,
    DiscoveredFact,
    GameSnapshot,
    GenieWish,
    HistoryChapter,
    NarrativeEntry,
    Npc,
    NpcEncounterLogTag,
    NpcPatch,
    TropeState,
    WorldStatePatch,
)
from sidequest.game.thresholds import detect_crossings
from sidequest.game.turn import PreprocessedAction, TurnManager, TurnPhase

__all__ = [
    # character
    "AbilityDefinition",
    "AffinityState",
    "Character",
    "KnownFact",
    # combatant
    "Combatant",
    # commands
    "BUILTIN_COMMANDS",
    "CommandHandler",
    "CommandResult",
    "DisplayResult",
    "ErrorResult",
    "GmCommand",
    "InventoryCommand",
    "MapCommand",
    "QuestsCommand",
    "SaveCommand",
    "StateMutationResult",
    "StatusCommand",
    # creature_core
    "PLACEHOLDER_EDGE_BASE_MAX",
    "CreatureCore",
    "EdgePool",
    "EdgeThreshold",
    "Inventory",
    "RecoveryTrigger",
    "placeholder_edge_pool",
    # delta
    "StateDelta",
    "StateSnapshot",
    "compute_delta",
    "snapshot",
    # monster_manual (ADR-059)
    "EntryState",
    "ManualEncounter",
    "ManualNpc",
    "MonsterManual",
    # encounter
    "EncounterActor",
    "EncounterMetric",
    "EncounterPhase",
    "RigType",
    "SecondaryStats",
    "StatValue",
    "StructuredEncounter",
    # persistence
    "DatabaseError",
    "NotFoundError",
    "PersistError",
    "SavedSession",
    "SerializationError",
    "SessionMeta",
    "SqliteStore",
    # resource_pool (ADR-033)
    "NotVoluntary",
    "ResourcePatch",
    "ResourcePatchError",
    "ResourcePatchOp",
    "ResourcePatchResult",
    "ResourcePool",
    "ResourceThreshold",
    "UnknownResource",
    "detect_crossings",
    "mint_threshold_lore",
    # session
    "AchievementTracker",
    "AxisValue",
    "DiscoveredFact",
    "GameSnapshot",
    "GenieWish",
    "HistoryChapter",
    "NarrativeEntry",
    "Npc",
    "NpcEncounterLogTag",
    "NpcPatch",
    "NpcPoolMember",
    "TropeState",
    "WorldStatePatch",
    # turn
    "PreprocessedAction",
    "TurnManager",
    "TurnPhase",
]
