"""sidequest.game — Phase 1 minimal slice of the game engine.

Port of sidequest_game crate (selected modules).
ADR-082: Python server narration vertical slice.

Phase 1 exports:
- Character, CreatureCore, EdgePool, Inventory
- GameSnapshot, WorldStatePatch, NpcPatch, NpcRegistryEntry, NarrativeEntry
- StateDelta (game-layer), StateSnapshot, snapshot, compute_delta
- TurnManager, TurnPhase
- CommandHandler, CommandResult, BUILTIN_COMMANDS
- SqliteStore, SavedSession, SessionMeta, PersistError

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
    MetricDirection,
    RigType,
    SecondaryStats,
    StatValue,
    StructuredEncounter,
)
from sidequest.game.persistence import (
    DatabaseError,
    NotFoundError,
    PersistError,
    SavedSession,
    SerializationError,
    SessionMeta,
    SqliteStore,
    db_path_for_session,
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
    EncounterTag,
    GameSnapshot,
    GenieWish,
    HistoryChapter,
    NarrativeEntry,
    Npc,
    NpcPatch,
    NpcRegistryEntry,
    TropeState,
    WorldStatePatch,
)
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
    # encounter
    "EncounterActor",
    "EncounterMetric",
    "EncounterPhase",
    "MetricDirection",
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
    "db_path_for_session",
    # resource_pool (story 42-2 — ADR-033 port)
    "NotVoluntary",
    "ResourcePatch",
    "ResourcePatchError",
    "ResourcePatchOp",
    "ResourcePatchResult",
    "ResourcePool",
    "ResourceThreshold",
    "UnknownResource",
    "mint_threshold_lore",
    # session
    "AchievementTracker",
    "AxisValue",
    "DiscoveredFact",
    "EncounterTag",
    "GameSnapshot",
    "GenieWish",
    "HistoryChapter",
    "NarrativeEntry",
    "Npc",
    "NpcPatch",
    "NpcRegistryEntry",
    "TropeState",
    "WorldStatePatch",
    # turn
    "PreprocessedAction",
    "TurnManager",
    "TurnPhase",
]
