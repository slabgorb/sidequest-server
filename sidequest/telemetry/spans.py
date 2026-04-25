"""OTEL span name catalog.

One constant per span emitted by Rust modules. Values are byte-identical to the
Rust tree's info_span!("...") name strings. Helpers wrap
tracer.start_as_current_span with type-safe attribute declarations.

Span groups mirror the Rust source crate that emits them:
  - turn.*            sidequest-server/dispatch/mod.rs, dispatch/tropes.rs
  - narrator.*        sidequest-server/dispatch/barrier.rs
  - orchestrator.*    sidequest-agents/orchestrator.rs
  - agent.*           sidequest-agents/client.rs
  - turn.agent_llm.*  sidequest-agents/orchestrator.rs
  - content.*         sidequest-genre/resolver/otel.rs
  - trope.*           sidequest-game/trope.rs, sidequest-agents/agents/troper.rs
  - barrier.*         sidequest-game/barrier.rs, sidequest-server/dispatch/barrier.rs
  - music_*           sidequest-game/music_director.rs
  - persistence_*     sidequest-game/persistence.rs
  - chargen.*         sidequest-game/builder.rs
  - npc_*             sidequest-game/npc.rs
  - creature.*        sidequest-game/creature_core.rs
  - disposition.*     sidequest-game/disposition.rs
  - compute_delta     sidequest-game/delta.rs
  - apply_world_patch sidequest-game/state.rs
  - quest_update      sidequest-game/state.rs
  - merchant.*        sidequest-agents/orchestrator.rs, sidequest-game/state.rs
  - inventory.*       sidequest-agents/inventory_extractor.rs
  - continuity.*      sidequest-agents/continuity_validator.rs
  - compose           sidequest-agents/context_builder.rs
  - world.*           sidequest-agents/agents/world_builder.rs
  - rag.*             sidequest-agents/orchestrator.rs
  - script_tool.*     sidequest-agents/orchestrator.rs
  - reminder_*        sidequest-server/dispatch/connect.rs, sidequest-server/lib.rs
  - pregen.*          sidequest-server/dispatch/pregen.rs
  - catch_up.*        sidequest-server/dispatch/catch_up.rs
  - npc.registration      sidequest-server/dispatch/npc_registry.rs
  - npc.auto_registered   sidequest-server/session_handler.py (story 37-44)
  - npc.reinvented        sidequest-server/session_handler.py (story 37-44)
  - scenario.*        sidequest-server/dispatch/mod.rs, dispatch/slash.rs
  - monster_manual.*  sidequest-server/dispatch/mod.rs
  - turn.slash_command sidequest-server/dispatch/slash.rs
  - combat.*          sidequest-server/dispatch/{response,state_mutations,telemetry}.rs
  - encounter.*       sidequest-game/encounter.rs (Story 3.4)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from opentelemetry import trace

from sidequest.telemetry.setup import tracer


class _SpanLike(Protocol):
    """Structural stand-in for opentelemetry.sdk.trace.ReadableSpan."""

    name: str
    attributes: dict[str, Any] | None


@dataclass(frozen=True)
class SpanRoute:
    """Routing decision for a span family.

    The translator consults the SPAN_ROUTES dict keyed by span name. When a
    span closes, if its name is in the dict, the matching SpanRoute is used
    to emit a typed WatcherEvent IN ADDITION TO the always-on
    agent_span_close fan-out. The extractor pulls the typed event's
    `fields` from the span's attributes — span attributes are the single
    source of truth for typed-event payloads.
    """

    event_type: str
    component: str
    extract: Callable[[_SpanLike], dict[str, Any]]


# Spans that intentionally have no typed-event route. Closing one of these
# emits agent_span_close only — they carry timing data but no semantic
# payload the dashboard needs to classify. Membership is a deliberate
# decision, enforced by tests/telemetry/test_routing_completeness.py.
FLAT_ONLY_SPANS: set[str] = set()


# Span name -> SpanRoute. Populated near each SPAN_* constant below so
# that renaming a constant breaks the route at import time, and a new
# constant without a routing decision trips the completeness lint test.
SPAN_ROUTES: dict[str, SpanRoute] = {}


# ---------------------------------------------------------------------------
# Turn — sidequest-server/dispatch/mod.rs, dispatch/tropes.rs
# ---------------------------------------------------------------------------
SPAN_TURN = "turn"
SPAN_TURN_BARRIER = "turn.barrier"
SPAN_TURN_STATE_UPDATE = "turn.state_update"
SPAN_TURN_SYSTEM_TICK = "turn.system_tick"
SPAN_TURN_SYSTEM_TICK_TROPES = "turn.system_tick.tropes"
SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT = "turn.system_tick.beat_context"
SPAN_TURN_MEDIA = "turn.media"
SPAN_TURN_TROPES = "turn.tropes"
SPAN_TURN_PHASE_TRANSITION = "turn.phase_transition"
SPAN_TURN_SLASH_COMMAND = "turn.slash_command"
SPAN_TURN_PREPROCESS_LLM = "turn.preprocess.llm"
SPAN_TURN_PREPROCESS_PARSE = "turn.preprocess.parse"
SPAN_TURN_PREPROCESS_WISH_CHECK = "turn.preprocess.wish_check"
SPAN_TURN_ASSEMBLE = "turn.assemble"

# ---------------------------------------------------------------------------
# Narrator — sidequest-server/dispatch/barrier.rs
# ---------------------------------------------------------------------------
SPAN_NARRATOR_SEALED_ROUND = "narrator.sealed_round"

# ---------------------------------------------------------------------------
# Orchestrator — sidequest-agents/orchestrator.rs
# ---------------------------------------------------------------------------
SPAN_ORCHESTRATOR_PROCESS_ACTION = "orchestrator.process_action"
SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET = "orchestrator.narrator_session_reset"
SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION = "orchestrator.genre_identity_injection"
SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION = "orchestrator.tactical_grid_injection"
SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION = "orchestrator.trope_beat_injection"
SPAN_ORCHESTRATOR_PARTY_PEER_INJECTION = "orchestrator.party_peer_injection"
SPAN_ORCHESTRATOR_LORE_FILTER = "orchestrator.lore_filter"

# ---------------------------------------------------------------------------
# Agent Claude subprocess calls — sidequest-agents/client.rs
# ---------------------------------------------------------------------------
SPAN_AGENT_CALL = "agent.call"
FLAT_ONLY_SPANS.add(SPAN_AGENT_CALL)
SPAN_AGENT_CALL_SESSION = "agent.call.session"
FLAT_ONLY_SPANS.add(SPAN_AGENT_CALL_SESSION)

# ---------------------------------------------------------------------------
# Turn LLM pipeline — sidequest-agents/orchestrator.rs
# ---------------------------------------------------------------------------
SPAN_TURN_AGENT_LLM_PROMPT_BUILD = "turn.agent_llm.prompt_build"
SPAN_TURN_AGENT_LLM_INFERENCE = "turn.agent_llm.inference"
FLAT_ONLY_SPANS.add(SPAN_TURN_AGENT_LLM_INFERENCE)
SPAN_TURN_AGENT_LLM_PARSE_RESPONSE = "turn.agent_llm.parse_response"

# ---------------------------------------------------------------------------
# Content resolution — sidequest-genre/resolver/otel.rs
# ---------------------------------------------------------------------------
SPAN_CONTENT_RESOLVE = "content.resolve"

# ---------------------------------------------------------------------------
# Trope engine — sidequest-game/trope.rs, sidequest-agents/agents/troper.rs
# ---------------------------------------------------------------------------
SPAN_TROPE_TICK = "trope_tick"
SPAN_TROPE_TICK_PER = "trope.tick"
SPAN_TROPE_ROOM_TICK = "trope.room_tick"
SPAN_TROPE_ACTIVATE = "trope_activate"
SPAN_TROPE_RESOLVE = "trope_resolve"
SPAN_TROPE_CROSS_SESSION = "trope.cross_session"
SPAN_TROPE_EVALUATE_TRIGGERS = "trope.evaluate_triggers"

# ---------------------------------------------------------------------------
# Barrier — sidequest-game/barrier.rs, sidequest-server/dispatch/barrier.rs
# ---------------------------------------------------------------------------
SPAN_BARRIER_ACTIVATED = "barrier.activated"
SPAN_BARRIER_RESOLVED = "barrier.resolved"

# ---------------------------------------------------------------------------
# Music / audio — sidequest-game/music_director.rs
# ---------------------------------------------------------------------------
SPAN_MUSIC_EVALUATE = "music_evaluate"
SPAN_MUSIC_CLASSIFY_MOOD = "music_classify_mood"

# ---------------------------------------------------------------------------
# Persistence — sidequest-game/persistence.rs
# ---------------------------------------------------------------------------
SPAN_PERSISTENCE_SAVE = "persistence_save"
SPAN_PERSISTENCE_LOAD = "persistence_load"
SPAN_PERSISTENCE_DELETE = "persistence_delete"

# ---------------------------------------------------------------------------
# Character generation — sidequest-game/builder.rs
# ---------------------------------------------------------------------------
SPAN_CHARGEN_STAT_ROLL = "chargen.stat_roll"
SPAN_CHARGEN_STATS_GENERATED = "chargen.stats_generated"
SPAN_CHARGEN_HP_FORMULA = "chargen.hp_formula"
SPAN_CHARGEN_BACKSTORY_COMPOSED = "chargen.backstory_composed"

# ---------------------------------------------------------------------------
# NPC — sidequest-game/npc.rs, sidequest-server/dispatch/npc_registry.rs
# ---------------------------------------------------------------------------
SPAN_NPC_MERGE_PATCH = "npc_merge_patch"
SPAN_NPC_REGISTRATION = "npc.registration"
SPAN_NPC_AUTO_REGISTERED = "npc.auto_registered"
SPAN_NPC_REINVENTED = "npc.reinvented"

# ---------------------------------------------------------------------------
# Creature — sidequest-game/creature_core.rs
# ---------------------------------------------------------------------------
SPAN_CREATURE_HP_DELTA = "creature.hp_delta"

# ---------------------------------------------------------------------------
# Disposition — sidequest-game/disposition.rs
# ---------------------------------------------------------------------------
SPAN_DISPOSITION_SHIFT = "disposition.shift"

# ---------------------------------------------------------------------------
# State patches — sidequest-game/state.rs
# ---------------------------------------------------------------------------
SPAN_APPLY_WORLD_PATCH = "apply_world_patch"
SPAN_QUEST_UPDATE = "quest_update"
SPAN_BUILD_PROTOCOL_DELTA = "build_protocol_delta"

# ---------------------------------------------------------------------------
# Delta — sidequest-game/delta.rs
# ---------------------------------------------------------------------------
SPAN_COMPUTE_DELTA = "compute_delta"

# ---------------------------------------------------------------------------
# Merchant — sidequest-agents/orchestrator.rs, sidequest-game/state.rs
# ---------------------------------------------------------------------------
SPAN_MERCHANT_CONTEXT_INJECTED = "merchant.context_injected"
SPAN_MERCHANT_TRANSACTION = "merchant.transaction"

# ---------------------------------------------------------------------------
# Inventory — sidequest-agents/inventory_extractor.rs
# ---------------------------------------------------------------------------
SPAN_INVENTORY_EXTRACTION = "inventory.extraction"

# ---------------------------------------------------------------------------
# Continuity — sidequest-agents/continuity_validator.rs
# ---------------------------------------------------------------------------
SPAN_CONTINUITY_LLM_VALIDATION = "continuity.llm_validation"

# ---------------------------------------------------------------------------
# Context builder — sidequest-agents/context_builder.rs
# ---------------------------------------------------------------------------
SPAN_COMPOSE = "compose"

# ---------------------------------------------------------------------------
# World builder — sidequest-agents/agents/world_builder.rs
# ---------------------------------------------------------------------------
SPAN_WORLD_MATERIALIZED = "world.materialized"

# ---------------------------------------------------------------------------
# RAG / prose — sidequest-agents/orchestrator.rs
# ---------------------------------------------------------------------------
SPAN_RAG_PROSE_CLEANUP = "rag.prose_cleanup"

# ---------------------------------------------------------------------------
# Script tool — sidequest-agents/orchestrator.rs
# ---------------------------------------------------------------------------
SPAN_SCRIPT_TOOL_PROMPT_INJECTED = "script_tool.prompt_injected"

# ---------------------------------------------------------------------------
# Turn reminder — sidequest-server/dispatch/connect.rs, lib.rs
# ---------------------------------------------------------------------------
SPAN_REMINDER_SPAWNED = "reminder_spawned"
SPAN_REMINDER_FIRED = "reminder_fired"

# ---------------------------------------------------------------------------
# Pregen — sidequest-server/dispatch/pregen.rs
# ---------------------------------------------------------------------------
SPAN_PREGEN_SEED_MANUAL = "pregen.seed_manual"

# ---------------------------------------------------------------------------
# Catch-up narration — sidequest-server/dispatch/catch_up.rs
# ---------------------------------------------------------------------------
SPAN_CATCH_UP_GENERATE = "catch_up.generate"

# ---------------------------------------------------------------------------
# Scenario — sidequest-server/dispatch/mod.rs, dispatch/slash.rs
# ---------------------------------------------------------------------------
SPAN_SCENARIO_ADVANCE = "scenario.advance"
SPAN_SCENARIO_ACCUSATION = "scenario.accusation"

# ---------------------------------------------------------------------------
# Monster manual — sidequest-server/dispatch/mod.rs
# ---------------------------------------------------------------------------
SPAN_MONSTER_MANUAL_INJECTED = "monster_manual.injected"

# ---------------------------------------------------------------------------
# Multiplayer lifecycle — sidequest-server/rest.py, session_handler.py
# Emitted on REST /api/games, WS slug-connect, PLAYER_SEAT, and pause gate
# decisions so the GM panel can verify MP wiring without divining from logs.
# ---------------------------------------------------------------------------
SPAN_MP_GAME_CREATED = "mp.game_created"
SPAN_MP_SLUG_CONNECT = "mp.slug_connect"
SPAN_MP_SEAT = "mp.seat"
SPAN_MP_PLAYER_ACTION_PAUSED = "mp.player_action_paused"

# Local DM (Group B) — decomposer + subsystem bank
# Emitted by sidequest/agents/local_dm.py and sidequest/agents/subsystems/__init__.py
# so the GM panel can verify the decomposer actually ran and which subsystems
# fired on a given turn (CLAUDE.md OTEL observability principle).
# ---------------------------------------------------------------------------
SPAN_LOCAL_DM_DECOMPOSE = "local_dm.decompose"
SPAN_ROUTES[SPAN_LOCAL_DM_DECOMPOSE] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.decompose",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "player_id": (span.attributes or {}).get("player_id", ""),
        "action_len": (span.attributes or {}).get("action_len", 0),
        "degraded": (span.attributes or {}).get("degraded", False),
        "degraded_reason": (span.attributes or {}).get("degraded_reason", ""),
    },
)
SPAN_LOCAL_DM_DISPATCH_BANK = "local_dm.dispatch_bank"
SPAN_ROUTES[SPAN_LOCAL_DM_DISPATCH_BANK] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.dispatch_bank",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "dispatch_count": (span.attributes or {}).get("dispatch_count", 0),
    },
)
SPAN_LOCAL_DM_SUBSYSTEM = "local_dm.subsystem"
SPAN_ROUTES[SPAN_LOCAL_DM_SUBSYSTEM] = SpanRoute(
    event_type="subsystem_exercise_summary",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.subsystem",
        "subsystem": (span.attributes or {}).get("subsystem", ""),
        "idempotency_key": (span.attributes or {}).get("idempotency_key", ""),
        "produced_directives": (span.attributes or {}).get("produced_directives", 0),
        "error": (span.attributes or {}).get("error", ""),
    },
)
SPAN_LOCAL_DM_LETHALITY_ARBITRATE = "local_dm.lethality_arbitrate"
SPAN_ROUTES[SPAN_LOCAL_DM_LETHALITY_ARBITRATE] = SpanRoute(
    event_type="state_transition",
    component="local_dm",
    extract=lambda span: {
        "field": "local_dm.lethality_arbitrate",
        "turn_id": (span.attributes or {}).get("turn_id", ""),
        "genre_key": (span.attributes or {}).get("genre_key", ""),
        "verdict_count": (span.attributes or {}).get("verdict_count", 0),
    },
)

# ---------------------------------------------------------------------------
# Helpers — context managers for Phase 1 spans
#
# Each helper accepts an optional ``_tracer`` parameter.  When omitted the
# production ``tracer()`` singleton is used.  Pass a provider-local tracer in
# tests to avoid fighting OpenTelemetry's single-provider-per-process rule.
# ---------------------------------------------------------------------------


@contextmanager
def turn_span(
    player_id: str,
    action: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_TURN with standard attrs."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_TURN,
        attributes={"player_id": player_id, "action": action[:80], **attrs},
    ) as span:
        yield span


@contextmanager
def orchestrator_process_action_span(
    action_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ORCHESTRATOR_PROCESS_ACTION."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ORCHESTRATOR_PROCESS_ACTION,
        attributes={"action_len": action_len, **attrs},
    ) as span:
        yield span


@contextmanager
def agent_call_span(
    model: str,
    prompt_len: int,
    *,
    backend: str = "claude-cli",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_AGENT_CALL."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_AGENT_CALL,
        attributes={
            "model": model,
            "prompt_len": prompt_len,
            "agent.backend": backend,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def agent_call_session_span(
    model: str,
    prompt_len: int,
    *,
    backend: str = "claude-cli",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_AGENT_CALL_SESSION with persistent session attrs."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_AGENT_CALL_SESSION,
        attributes={
            "model": model,
            "prompt_len": prompt_len,
            "agent.backend": backend,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def content_resolve_span(
    axis: str,
    field_path: str,
    genre: str,
    world: str = "",
    culture: str = "",
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_CONTENT_RESOLVE with content provenance attrs."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_CONTENT_RESOLVE,
        attributes={
            "content.axis": axis,
            "content.field_path": field_path,
            "content.genre": genre,
            "content.world": world,
            "content.culture": culture,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def persistence_save_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_PERSISTENCE_SAVE."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_PERSISTENCE_SAVE,
        attributes={"genre": genre, "world": world, "player": player, **attrs},
    ) as span:
        yield span


@contextmanager
def persistence_load_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_PERSISTENCE_LOAD."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_PERSISTENCE_LOAD,
        attributes={"genre": genre, "world": world, "player": player, **attrs},
    ) as span:
        yield span


@contextmanager
def trope_tick_span(
    trope_count: int,
    multiplier: float,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_TROPE_TICK."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_TROPE_TICK,
        attributes={"trope_count": trope_count, "multiplier": multiplier, **attrs},
    ) as span:
        yield span


@contextmanager
def turn_agent_llm_inference_span(
    model: str,
    prompt_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_TURN_AGENT_LLM_INFERENCE."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_TURN_AGENT_LLM_INFERENCE,
        attributes={"model": model, "prompt_len": prompt_len, **attrs},
    ) as span:
        yield span


# ---------------------------------------------------------------------------
# Multiplayer lifecycle helpers
# ---------------------------------------------------------------------------


@contextmanager
def mp_game_created_span(
    slug: str,
    mode: str,
    genre_slug: str,
    world_slug: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_MP_GAME_CREATED.

    Emitted for every POST /api/games call. The ``resumed`` attr tells the GM
    panel whether this returned an existing game (200) or created a new one (201).
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_MP_GAME_CREATED,
        attributes={
            "slug": slug,
            "mode": mode,
            "genre_slug": genre_slug,
            "world_slug": world_slug,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def mp_slug_connect_span(
    slug: str,
    player_id: str,
    mode: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_MP_SLUG_CONNECT.

    Emitted when a WebSocket performs the slug-based connect handshake.
    Carries pause-resolution attrs (``was_paused_before``, ``resolved_pause``)
    so the GM panel can see which reconnects woke the room up.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_MP_SLUG_CONNECT,
        attributes={
            "slug": slug,
            "player_id": player_id,
            "mode": mode,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def mp_seat_span(
    slug: str,
    player_id: str,
    character_slot: str | None,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_MP_SEAT.

    Emitted on PLAYER_SEAT. ``character_slot`` may be None for observer seats.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_MP_SEAT,
        attributes={
            "slug": slug,
            "player_id": player_id,
            "character_slot": character_slot if character_slot is not None else "",
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def mp_player_action_paused_span(
    slug: str,
    player_id: str,
    absent_player_ids: list[str],
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_MP_PLAYER_ACTION_PAUSED.

    Emitted when the pause gate blocks a PLAYER_ACTION because a seated
    player is absent. The GM panel uses this to verify the pause mechanism
    is actually running (not just that the UI rendered a paused banner).
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_MP_PLAYER_ACTION_PAUSED,
        attributes={
            "slug": slug,
            "player_id": player_id,
            "absent_count": len(absent_player_ids),
            "absent_player_ids": ",".join(absent_player_ids),
            **attrs,
        },
    ) as span:
        yield span



# ---------------------------------------------------------------------------
# Local DM (Group B) helpers — decomposer + subsystem bank
# ---------------------------------------------------------------------------


@contextmanager
def local_dm_decompose_span(
    turn_id: str,
    player_id: str,
    action_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOCAL_DM_DECOMPOSE.

    Emitted by LocalDM.decompose for every decomposer invocation. The
    ``degraded`` + ``degraded_reason`` attrs are set by the caller before
    return so the GM panel can see which turns fell back to the degraded
    package (spec §6.6) vs. a clean structured output.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOCAL_DM_DECOMPOSE,
        attributes={
            "turn_id": turn_id,
            "player_id": player_id,
            "action_len": action_len,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def local_dm_dispatch_bank_span(
    turn_id: str,
    dispatch_count: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOCAL_DM_DISPATCH_BANK.

    Emitted once per run_dispatch_bank call. Parent of every
    ``local_dm.subsystem`` span for that turn, so the GM panel can
    count dispatches per turn without joining across traces.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOCAL_DM_DISPATCH_BANK,
        attributes={
            "turn_id": turn_id,
            "dispatch_count": dispatch_count,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def local_dm_subsystem_span(
    subsystem: str,
    idempotency_key: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOCAL_DM_SUBSYSTEM.

    Emitted once per subsystem invocation inside run_dispatch_bank.
    The caller records ``produced_directives`` (int) on success or
    an ``error`` attr on the failure path — this is the lie detector
    for whether a subsystem actually ran end-to-end.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOCAL_DM_SUBSYSTEM,
        attributes={
            "subsystem": subsystem,
            "idempotency_key": idempotency_key,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def lethality_arbitrate_span(
    turn_id: str,
    genre_key: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOCAL_DM_LETHALITY_ARBITRATE.

    Emitted once per LethalityArbiter.arbitrate call (Group C). The caller
    sets ``verdict_count`` on the span before return so the GM panel can
    see how many lethality verdicts synthesised this turn — Sebastien's
    lie detector for whether the arbiter actually ran vs. the narrator
    improvising survival.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOCAL_DM_LETHALITY_ARBITRATE,
        attributes={
            "turn_id": turn_id,
            "genre_key": genre_key,
            **attrs,
        },
    ) as span:
        yield span


# ---------------------------------------------------------------------------
# Combat / Encounter — dispatch/{response,state_mutations,telemetry}.rs +
# sidequest-game/encounter.rs (Story 3.4). Names byte-identical to Rust
# watcher!("...") emitters — GM-panel queries break on drift.
# ---------------------------------------------------------------------------
SPAN_COMBAT_TICK = "combat.tick"
SPAN_ROUTES[SPAN_COMBAT_TICK] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.tick",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "beat": (span.attributes or {}).get("beat", 0),
        "phase": (span.attributes or {}).get("phase", ""),
    },
)
SPAN_COMBAT_ENDED = "combat.ended"
SPAN_ROUTES[SPAN_COMBAT_ENDED] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.ended",
        "outcome": (span.attributes or {}).get("outcome", ""),
        "duration_beats": (span.attributes or {}).get("duration_beats", 0),
    },
)
SPAN_COMBAT_PLAYER_DEAD = "combat.player_dead"
SPAN_ROUTES[SPAN_COMBAT_PLAYER_DEAD] = SpanRoute(
    event_type="state_transition",
    component="combat",
    extract=lambda span: {
        "field": "combat.player_dead",
        "player_name": (span.attributes or {}).get("player_name", ""),
    },
)
SPAN_ENCOUNTER_PHASE_TRANSITION = "encounter.phase_transition"
SPAN_ROUTES[SPAN_ENCOUNTER_PHASE_TRANSITION] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.phase_transition",
        # Emission site uses keys "from" and "to" (encounter_phase_transition_span)
        "from_phase": (span.attributes or {}).get("from", ""),
        "to_phase": (span.attributes or {}).get("to", ""),
    },
)
SPAN_ENCOUNTER_RESOLVED = "encounter.resolved"
SPAN_ROUTES[SPAN_ENCOUNTER_RESOLVED] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.resolved",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "outcome": (span.attributes or {}).get("outcome", ""),
        "source": (span.attributes or {}).get("source", ""),
    },
)
SPAN_ENCOUNTER_BEAT_APPLIED = "encounter.beat_applied"
SPAN_ROUTES[SPAN_ENCOUNTER_BEAT_APPLIED] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.beat_applied",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "actor": (span.attributes or {}).get("actor", ""),
        "beat_id": (span.attributes or {}).get("beat_id", ""),
        "metric_delta": (span.attributes or {}).get("metric_delta", 0),
    },
)
SPAN_ENCOUNTER_CONFRONTATION_INITIATED = "encounter.confrontation_initiated"
SPAN_ROUTES[SPAN_ENCOUNTER_CONFRONTATION_INITIATED] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.confrontation_initiated",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "genre_slug": (span.attributes or {}).get("genre_slug", ""),
    },
)
SPAN_ENCOUNTER_EMPTY_ACTOR_LIST = "encounter.empty_actor_list"
SPAN_ROUTES[SPAN_ENCOUNTER_EMPTY_ACTOR_LIST] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.empty_actor_list",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "genre_slug": (span.attributes or {}).get("genre_slug", ""),
        "player_name": (span.attributes or {}).get("player_name", ""),
    },
)
SPAN_ENCOUNTER_BEAT_FAILURE_BRANCH = "encounter.beat_failure_branch"
SPAN_ROUTES[SPAN_ENCOUNTER_BEAT_FAILURE_BRANCH] = SpanRoute(
    event_type="state_transition",
    component="encounter",
    extract=lambda span: {
        "field": "encounter.beat_failure_branch",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "beat_id": (span.attributes or {}).get("beat_id", ""),
        "actor": (span.attributes or {}).get("actor", ""),
        "base_delta": (span.attributes or {}).get("base_delta", 0),
        "failure_delta": (span.attributes or {}).get("failure_delta", 0),
    },
)

# Dice dispatch (story 34-11) — names byte-identical to Rust
# ``emit_dice_request_sent`` / ``emit_dice_throw_received`` /
# ``emit_dice_result_broadcast`` so GM-panel queries line up.
# NOTE: these are span *events* added via span.add_event(), not standalone
# spans created with start_as_current_span. They attach to the enclosing
# turn span and are not routable as spans — no SPAN_ROUTES entry.
SPAN_DICE_REQUEST_SENT = "dice.request_sent"
FLAT_ONLY_SPANS.add(SPAN_DICE_REQUEST_SENT)
SPAN_DICE_THROW_RECEIVED = "dice.throw_received"
FLAT_ONLY_SPANS.add(SPAN_DICE_THROW_RECEIVED)
SPAN_DICE_RESULT_BROADCAST = "dice.result_broadcast"
FLAT_ONLY_SPANS.add(SPAN_DICE_RESULT_BROADCAST)


@contextmanager
def combat_tick_span(
    *,
    encounter_type: str,
    beat: int,
    phase: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_COMBAT_TICK.

    Fires every encounter turn with encounter type, beat number, and phase.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_COMBAT_TICK,
        attributes={
            "encounter_type": encounter_type,
            "beat": beat,
            "phase": phase,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def encounter_phase_transition_span(
    *,
    from_phase: str,
    to_phase: str,
    encounter_type: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_PHASE_TRANSITION.

    Fires when encounter phase changes with from/to phases and encounter type.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ENCOUNTER_PHASE_TRANSITION,
        attributes={
            "from": from_phase,
            "to": to_phase,
            "encounter_type": encounter_type,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def encounter_resolved_span(
    *,
    encounter_type: str,
    outcome: str | None,
    source: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_RESOLVED.

    Fires when encounter resolved flag flips True with outcome and source.
    """
    t = _tracer if _tracer is not None else tracer()
    span_attrs = {
        "encounter_type": encounter_type,
        "source": source,
    }
    if outcome is not None:
        span_attrs["outcome"] = outcome
    span_attrs.update(attrs)
    with t.start_as_current_span(
        SPAN_ENCOUNTER_RESOLVED,
        attributes=span_attrs,
    ) as span:
        yield span


@contextmanager
def encounter_beat_applied_span(
    *,
    encounter_type: str,
    actor: str,
    beat_id: str,
    metric_delta: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_BEAT_APPLIED.

    Fires when a narrator beat_selection is consumed with actor, beat_id, delta.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ENCOUNTER_BEAT_APPLIED,
        attributes={
            "encounter_type": encounter_type,
            "actor": actor,
            "beat_id": beat_id,
            "metric_delta": metric_delta,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def encounter_confrontation_initiated_span(
    *,
    encounter_type: str,
    genre_slug: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_CONFRONTATION_INITIATED.

    Fires when a narrator-emitted confrontation=... triggers encounter instantiation.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ENCOUNTER_CONFRONTATION_INITIATED,
        attributes={
            "encounter_type": encounter_type,
            "genre_slug": genre_slug,
            **attrs,
        },
    ) as span:
        yield span


def emit_dice_request_sent(
    *,
    request_id: str,
    rolling_player_id: str,
    stat: str,
    difficulty: int,
    modifier: int,
) -> None:
    """Fire an event on the current span when a DiceRequest is broadcast.

    Mirrors ``emit_dice_request_sent`` in sidequest-server/src/lib.rs. GM-panel
    "lie detector" — every DiceRequest we send to the room leaves a trail.
    """
    span = trace.get_current_span()
    span.add_event(
        SPAN_DICE_REQUEST_SENT,
        attributes={
            "request_id": request_id,
            "rolling_player_id": rolling_player_id,
            "stat": stat,
            "difficulty": int(difficulty),
            "modifier": int(modifier),
        },
    )


def emit_dice_throw_received(
    *,
    request_id: str,
    rolling_player_id: str,
    face: list[int],
) -> None:
    """Fire on receipt of a DICE_THROW after correlation to a pending request.

    Mirrors ``emit_dice_throw_received`` in the Rust server. Fires only after
    the pending request was found — so absence of this span on a known
    request_id is a real correlation drop, not noise.
    """
    span = trace.get_current_span()
    span.add_event(
        SPAN_DICE_THROW_RECEIVED,
        attributes={
            "request_id": request_id,
            "rolling_player_id": rolling_player_id,
            "face": list(face),
        },
    )


def emit_dice_result_broadcast(
    *,
    request_id: str,
    rolling_player_id: str,
    total: int,
    outcome: str,
    seed: int,
) -> None:
    """Fire when a DiceResult is resolved + broadcast.

    Mirrors ``emit_dice_result_broadcast`` in the Rust server — the final
    "what actually happened" span for every roll, so the GM panel can verify
    physics-is-the-roll end-to-end without trusting narrator self-reporting.
    """
    span = trace.get_current_span()
    span.add_event(
        SPAN_DICE_RESULT_BROADCAST,
        attributes={
            "request_id": request_id,
            "rolling_player_id": rolling_player_id,
            "total": int(total),
            "outcome": outcome,
            "seed": int(seed),
        },
    )


@contextmanager
def encounter_beat_failure_branch_span(
    *,
    encounter_type: str,
    beat_id: str,
    actor: str,
    base_delta: int,
    failure_delta: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_BEAT_FAILURE_BRANCH.

    Fires when a beat's failure branch is taken — i.e. a dice roll
    classified as Fail / CritFail and the engine substituted
    ``failure_metric_delta`` for the default ``metric_delta``. Lets the GM
    panel surface when a beat's risk clause actually paid out, vs the
    narrator merely saying a roll failed without mechanical consequence.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ENCOUNTER_BEAT_FAILURE_BRANCH,
        attributes={
            "encounter_type": encounter_type,
            "beat_id": beat_id,
            "actor": actor,
            "base_delta": base_delta,
            "failure_delta": failure_delta,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def encounter_empty_actor_list_span(
    *,
    encounter_type: str,
    genre_slug: str,
    player_name: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_ENCOUNTER_EMPTY_ACTOR_LIST.

    Fires when the narrator emits ``confrontation=...`` but the structured
    extraction has no ``npcs_present`` entries, so the encounter is
    instantiated with only the player in the combatant list. This indicates
    a narrator-extraction lie: the prose names adversaries but the JSON
    game_patch omits them. Confrontation panel will render only the player.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_ENCOUNTER_EMPTY_ACTOR_LIST,
        attributes={
            "encounter_type": encounter_type,
            "genre_slug": genre_slug,
            "player_name": player_name,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def combat_ended_span(
    *,
    outcome: str,
    duration_beats: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_COMBAT_ENDED.

    Fires when encounter resolves (any outcome) with outcome and duration.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_COMBAT_ENDED,
        attributes={
            "outcome": outcome,
            "duration_beats": duration_beats,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def combat_player_dead_span(
    *,
    player_name: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_COMBAT_PLAYER_DEAD.

    Fires on player fatality resolution with player name.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_COMBAT_PLAYER_DEAD,
        attributes={
            "player_name": player_name,
            **attrs,
        },
    ) as span:
        yield span


# ---------------------------------------------------------------------------
# Projection — sidequest/game/projection/*
# ---------------------------------------------------------------------------
SPAN_PROJECTION_DECIDE = "projection.filter.decide"
SPAN_ROUTES[SPAN_PROJECTION_DECIDE] = SpanRoute(
    event_type="state_transition",
    component="projection",
    extract=lambda span: {
        "field": "projection.filter.decide",
        "player_id": (span.attributes or {}).get("player_id", ""),
        "event_kind": (span.attributes or {}).get("event.kind", ""),
        "event_seq": (span.attributes or {}).get("event.seq", 0),
        "decision_include": (span.attributes or {}).get("decision.include", None),
        "rule_source": (span.attributes or {}).get("rule.source", ""),
    },
)
SPAN_PROJECTION_CACHE_FILL = "projection.cache.fill"
SPAN_ROUTES[SPAN_PROJECTION_CACHE_FILL] = SpanRoute(
    event_type="state_transition",
    component="projection",
    extract=lambda span: {
        "field": "projection.cache.fill",
        "player_id": (span.attributes or {}).get("player_id", ""),
        "event_seq": (span.attributes or {}).get("event.seq", 0),
    },
)
SPAN_PROJECTION_CACHE_LAZY_FILL = "projection.cache.lazy_fill"
SPAN_ROUTES[SPAN_PROJECTION_CACHE_LAZY_FILL] = SpanRoute(
    event_type="state_transition",
    component="projection",
    extract=lambda span: {
        "field": "projection.cache.lazy_fill",
        "player_id": (span.attributes or {}).get("player_id", ""),
        "events_filled": (span.attributes or {}).get("events_filled", 0),
    },
)


@contextmanager
def projection_decide_span(
    *,
    event_kind: str,
    event_seq: int | None,
    player_id: str,
    _tracer: trace.Tracer | None = None,
) -> Iterator[trace.Span]:
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "event.kind": event_kind,
        "player_id": player_id,
    }
    if event_seq is not None:
        attributes["event.seq"] = event_seq
    with t.start_as_current_span(SPAN_PROJECTION_DECIDE, attributes=attributes) as span:
        yield span


@contextmanager
def projection_cache_fill_span(
    *, event_seq: int, player_id: str, _tracer: trace.Tracer | None = None
) -> Iterator[trace.Span]:
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_PROJECTION_CACHE_FILL,
        attributes={"event.seq": event_seq, "player_id": player_id},
    ) as span:
        yield span


@contextmanager
def projection_cache_lazy_fill_span(
    *, player_id: str, _tracer: trace.Tracer | None = None
) -> Iterator[trace.Span]:
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_PROJECTION_CACHE_LAZY_FILL,
        attributes={"player_id": player_id},
    ) as span:
        yield span
