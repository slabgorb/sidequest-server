# Game Phase 1 Slice — Port Notes

Story 41-3. Narration vertical slice of `sidequest-game` crate.

## What Was Ported

| Python module | Rust source | Notes |
|---|---|---|
| `sidequest/game/creature_core.py` | `creature_core.rs` (129 LOC) | EdgePool, EdgeThreshold, RecoveryTrigger, CreatureCore, Inventory |
| `sidequest/game/character.py` | `character.rs` (314 LOC) | Character, AbilityDefinition, KnownFact, AffinityState |
| `sidequest/game/turn.py` | `turn.rs` (165 LOC) | TurnPhase, TurnManager |
| `sidequest/game/delta.py` | `delta.rs` (212 LOC) | StateDelta, StateSnapshot, snapshot(), compute_delta() |
| `sidequest/game/commands.py` | `commands.rs` (403 LOC) | CommandHandler, CommandResult, all six command handlers |
| `sidequest/game/session.py` | `state.rs` (919 LOC) | GameSnapshot, WorldStatePatch, NpcPatch, NarrativeEntry, Npc, NpcRegistryEntry, + deferred-subsystem data containers |
| `sidequest/game/persistence.py` | `persistence.rs` (581 LOC) | SqliteStore, SavedSession, SessionMeta, PersistError, db_path_for_session |

## Narrator-Relevant Fields (P1-Required)

From the agent import audit (`sidequest-agents/src/`):
- `character::Character` — name, class, race, stats, abilities, known_facts, is_friendly
- `state::GameSnapshot` — characters, npcs, location, time_of_day, quest_log, notes,
  narrative_log, atmosphere, current_region, discovered_regions, active_stakes,
  lore_established, turn_manager, npc_registry
- `delta::StateDelta` — boolean change flags (used for broadcast optimization)
- `turn::TurnManager` — round/interaction counters, phase

## Deferred Fields

### P2-deferred
- `Character.resolved_archetype / archetype_provenance` — chargen axis system (story G2)
- `Character.affinities` — affinity progression (Epic F8)
- `CreatureCore.acquired_advancements` — advancement tracking (epic 39-8)
- `EdgePool.recovery_triggers / thresholds` — combat/advancement (stories 39-4/5/6)
- `GameSnapshot.active_tropes` — trope engine (port separately, P2)
- `GameSnapshot.axis_values` — tone system (/tone command, F2/F10)

### P3-deferred
- `GameSnapshot.encounter` — StructuredEncounter / ADR-033 confrontation engine
- `GameSnapshot.campaign_maturity / world_history` — world materialization
- `GameSnapshot.discovered_rooms` — room-graph navigation (story 19-2)

### P4-deferred
- `GameSnapshot.resources` — named resource pools (story 16-10)

### P5-deferred
- `GameSnapshot.scenario_state` — Epic 7 whodunit / belief state / clues / accusations
- `GameSnapshot.genie_wishes` — consequence engine (F9)
- `Npc.ocean / belief_state` — OCEAN personality + scenario system

### P6-deferred
- `GameSnapshot.achievement_tracker` — achievement system (F7)
- `Character.affinities` — already noted above

## SQLite Compatibility

**Status: DONE_WITH_CONCERNS — Python round-trips work. Rust save loading is deferred.**

The Rust `SqliteStore` serializes `GameSnapshot` via `serde_json::to_string`, which
flattens `CreatureCore` fields to the parent level (Character/Npc) due to
`#[serde(flatten)]`. Example Rust JSON:

```json
{
  "name": "Thorn Ironhide",
  "description": "A scarred dwarf warrior",
  ...
  "backstory": "Raised in the iron mines"
}
```

The Python `GameSnapshot` uses nested `core: CreatureCore`, so the same data would be:

```json
{
  "core": {
    "name": "Thorn Ironhide",
    "description": "A scarred dwarf warrior"
  },
  "backstory": "Raised in the iron mines"
}
```

A migration shim is required to load Rust saves in Python: flatten the nested
`core` fields into the Character/Npc at the JSON level before calling
`GameSnapshot.model_validate_json()`.

**Schema is identical** — table names, column names, types all match Rust exactly.

## What 41-5 (Narrator Agent) Needs to Know

1. `GameSnapshot` is in `sidequest.game.session`. Import via `sidequest.game`.

2. `Character.core.name` — not `Character.name`. The Rust `name()` method is ported
   as a Python method, but the field is `character.core.name`.

3. The game-layer `StateDelta` (`sidequest.game.delta.StateDelta`) is boolean flags
   only. The wire-layer `StateDelta` (`sidequest.protocol.models.StateDelta`) carries
   actual data. Don't mix them. Use `compute_delta()` for change detection; use
   the protocol type for WebSocket messages.

4. `commands.py` is slash-command dispatch (`/status`, `/inventory`, etc.), not an
   intent routing enum. Narrator intents are handled elsewhere.

5. SQLite persistence is synchronous (`sqlite3` stdlib). The Rust version is async
   via a PersistenceWorker actor. The Python version is blocking-synchronous — wrap
   in an executor if calling from async context.

6. `GameSnapshot.model_config = {"extra": "ignore"}` — unknown fields in save files
   are silently dropped. This is intentional for forward/backward compatibility.
