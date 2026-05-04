"""sidequest.game — Phase 1 minimal slice of the game engine.

Phase 1 exports:
- Character, CreatureCore, EdgePool, EdgeThreshold, Inventory
- GameSnapshot, WorldStatePatch, NpcPatch, NpcRegistryEntry, NarrativeEntry
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
    NpcRegistryEntry,
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
    "NpcRegistryEntry",
    "TropeState",
    "WorldStatePatch",
    # turn
    "PreprocessedAction",
    "TurnManager",
    "TurnPhase",
]


# S4 deprecation alias — drop in the release after this one. External
# saves and pre-cleanup test fixtures still reference EncounterTag at this
# import path; keeping the alias one release prevents a hard cutover.
#
# Reviewer finding 2026-05-04 (LOW): the bare alias had no removal timer
# and no warning surface. Module ``__getattr__`` lets us emit a
# DeprecationWarning on every legacy import while still resolving to
# ``NpcEncounterLogTag`` — the deprecation is now visible to callers and
# to the test suite (``-W error::DeprecationWarning`` in CI would fail
# the moment a stale import sneaks in). Removal is tracked as a Wave 2
# chore story.
__all__.append("EncounterTag")


def __getattr__(name: str):  # pragma: no cover - thin shim
    if name == "EncounterTag":
        import warnings

        warnings.warn(
            "sidequest.game.EncounterTag was renamed to NpcEncounterLogTag in "
            "story 45-43 (Wave 1); the legacy name will be removed in Wave 2.",
            DeprecationWarning,
            stacklevel=2,
        )
        return NpcEncounterLogTag
    raise AttributeError(f"module 'sidequest.game' has no attribute {name!r}")
