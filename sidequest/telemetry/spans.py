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

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from sidequest.telemetry.setup import tracer

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
SPAN_ORCHESTRATOR_LORE_FILTER = "orchestrator.lore_filter"

# ---------------------------------------------------------------------------
# Agent Claude subprocess calls — sidequest-agents/client.rs
# ---------------------------------------------------------------------------
SPAN_AGENT_CALL = "agent.call"
SPAN_AGENT_CALL_SESSION = "agent.call.session"

# ---------------------------------------------------------------------------
# Turn LLM pipeline — sidequest-agents/orchestrator.rs
# ---------------------------------------------------------------------------
SPAN_TURN_AGENT_LLM_PROMPT_BUILD = "turn.agent_llm.prompt_build"
SPAN_TURN_AGENT_LLM_INFERENCE = "turn.agent_llm.inference"
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
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_AGENT_CALL."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_AGENT_CALL,
        attributes={"model": model, "prompt_len": prompt_len, **attrs},
    ) as span:
        yield span


@contextmanager
def agent_call_session_span(
    model: str,
    prompt_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_AGENT_CALL_SESSION with persistent session attrs."""
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_AGENT_CALL_SESSION,
        attributes={"model": model, "prompt_len": prompt_len, **attrs},
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
# Combat / Encounter — dispatch/{response,state_mutations,telemetry}.rs +
# sidequest-game/encounter.rs (Story 3.4). Names byte-identical to Rust
# watcher!("...") emitters — GM-panel queries break on drift.
# ---------------------------------------------------------------------------
SPAN_COMBAT_TICK = "combat.tick"
SPAN_COMBAT_ENDED = "combat.ended"
SPAN_COMBAT_PLAYER_DEAD = "combat.player_dead"
SPAN_ENCOUNTER_PHASE_TRANSITION = "encounter.phase_transition"
SPAN_ENCOUNTER_RESOLVED = "encounter.resolved"
SPAN_ENCOUNTER_BEAT_APPLIED = "encounter.beat_applied"
SPAN_ENCOUNTER_CONFRONTATION_INITIATED = "encounter.confrontation_initiated"


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
SPAN_PROJECTION_CACHE_FILL = "projection.cache.fill"
SPAN_PROJECTION_CACHE_LAZY_FILL = "projection.cache.lazy_fill"


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
