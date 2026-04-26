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
#
# Python-port note (ADR-082): ``SPAN_MUSIC_EVALUATE`` /
# ``SPAN_MUSIC_CLASSIFY_MOOD`` mirror the Rust ``music_director`` agent
# which the Python port did not reimplement; they stay in
# ``FLAT_ONLY_SPANS`` until that agent is ported. The four
# ``SPAN_AUDIO_*`` lifecycle spans below ARE live — they fire from the
# audio backend setup and per-turn cue dispatch in
# ``server/session_handler.py`` (the integrated cue pipeline replaces the
# standalone music-director agent in the port).
# ---------------------------------------------------------------------------
SPAN_MUSIC_EVALUATE = "music_evaluate"
SPAN_MUSIC_CLASSIFY_MOOD = "music_classify_mood"
SPAN_AUDIO_BACKEND_ENABLED = "audio.backend_enabled"
SPAN_ROUTES[SPAN_AUDIO_BACKEND_ENABLED] = SpanRoute(
    event_type="state_transition",
    component="audio",
    extract=lambda span: {
        "field": "audio",
        "op": "enabled",
        "genre": (span.attributes or {}).get("genre", ""),
        "mood_count": (span.attributes or {}).get("mood_count", 0),
        "sfx_count": (span.attributes or {}).get("sfx_count", 0),
    },
)
SPAN_AUDIO_BACKEND_DISABLED = "audio.backend_disabled"
SPAN_ROUTES[SPAN_AUDIO_BACKEND_DISABLED] = SpanRoute(
    event_type="state_transition",
    component="audio",
    extract=lambda span: {
        "field": "audio",
        "op": "disabled",
        "reason": (span.attributes or {}).get("reason", ""),
        "genre": (span.attributes or {}).get("genre", ""),
    },
)
SPAN_AUDIO_SKIPPED = "audio.skipped"
SPAN_ROUTES[SPAN_AUDIO_SKIPPED] = SpanRoute(
    event_type="state_transition",
    component="audio",
    extract=lambda span: {
        "field": "audio",
        "op": "skipped",
        "reason": (span.attributes or {}).get("reason", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "extra": (span.attributes or {}).get("extra_json", "{}"),
    },
)
SPAN_AUDIO_DISPATCHED = "audio.dispatched"
SPAN_ROUTES[SPAN_AUDIO_DISPATCHED] = SpanRoute(
    event_type="state_transition",
    component="audio",
    extract=lambda span: {
        "field": "audio",
        "op": "dispatched",
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "mood": (span.attributes or {}).get("mood", ""),
        "music_track": (span.attributes or {}).get("music_track", ""),
        "sfx_count": (span.attributes or {}).get("sfx_count", 0),
    },
)

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
#
# Python-port note (ADR-082): the Rust pipeline routed NPC mutations through
# explicit ``register_npc`` / ``merge_npc_patch`` calls. The Python port did
# not port those entry points; ``narration_apply.py`` mutates ``npc_registry``
# directly and ``session_helpers._detect_npc_identity_drift`` warns inline.
# ``SPAN_NPC_AUTO_REGISTERED`` and ``SPAN_NPC_REINVENTED`` are live (NPC
# bundle, 2026-04-25); ``SPAN_NPC_REGISTRATION`` and ``SPAN_NPC_MERGE_PATCH``
# remain in ``FLAT_ONLY_SPANS`` (below) until either the helpers are wired or
# the constants are removed.
# ---------------------------------------------------------------------------
SPAN_NPC_MERGE_PATCH = "npc_merge_patch"
SPAN_NPC_REGISTRATION = "npc.registration"
SPAN_NPC_AUTO_REGISTERED = "npc.auto_registered"
SPAN_ROUTES[SPAN_NPC_AUTO_REGISTERED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "auto_registered",
        "name": (span.attributes or {}).get("npc_name", ""),
        "pronouns": (span.attributes or {}).get("pronouns", ""),
        "role": (span.attributes or {}).get("role", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "registry_len": (span.attributes or {}).get("registry_len", 0),
    },
)
SPAN_NPC_REINVENTED = "npc.reinvented"
SPAN_ROUTES[SPAN_NPC_REINVENTED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "reinvented",
        "name": (span.attributes or {}).get("npc_name", ""),
        "drift_field": (span.attributes or {}).get("drift_field", ""),
        "expected": (span.attributes or {}).get("expected", ""),
        "narrator": (span.attributes or {}).get("narrator", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

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
#
# Python-port note (ADR-082): the Rust pipeline routed all narration-driven
# state mutations through ``GameSnapshot::apply_world_patch`` (a single typed
# WorldStatePatch carrying location/quest/inventory/etc). The Python port
# inlined those mutations directly in ``server/narration_apply.py`` —
# ``apply_world_patch`` and ``build_protocol_delta`` exist as ports of the
# Rust functions but have no production caller. They stay in
# ``FLAT_ONLY_SPANS`` (below) until either the helper is wired into the
# Python pipeline OR the constants are removed. ``SPAN_QUEST_UPDATE`` IS
# live — it fires from the quest_log block of ``narration_apply.py``.
# ---------------------------------------------------------------------------
SPAN_APPLY_WORLD_PATCH = "apply_world_patch"
SPAN_QUEST_UPDATE = "quest_update"
SPAN_ROUTES[SPAN_QUEST_UPDATE] = SpanRoute(
    event_type="state_transition",
    component="quest_log",
    extract=lambda span: {
        "field": "quest_log",
        "updates": (span.attributes or {}).get("updates_json", "{}"),
        "updates_count": (span.attributes or {}).get("updates_count", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)
SPAN_BUILD_PROTOCOL_DELTA = "build_protocol_delta"

# ---------------------------------------------------------------------------
# Delta — sidequest-game/delta.rs
# Python-port note: ``compute_delta`` exists in ``game/delta.py`` but has no
# production caller (port artifact — see SPAN_APPLY_WORLD_PATCH note above).
# Kept in ``FLAT_ONLY_SPANS`` until the function is either wired or removed.
# ---------------------------------------------------------------------------
SPAN_COMPUTE_DELTA = "compute_delta"

# ---------------------------------------------------------------------------
# Merchant — sidequest-agents/orchestrator.rs, sidequest-game/state.rs
# ---------------------------------------------------------------------------
SPAN_MERCHANT_CONTEXT_INJECTED = "merchant.context_injected"
SPAN_MERCHANT_TRANSACTION = "merchant.transaction"

# ---------------------------------------------------------------------------
# Inventory — sidequest-agents/inventory_extractor.rs
#
# Python-port note (ADR-082): ``SPAN_INVENTORY_EXTRACTION`` mirrors the Rust
# ``inventory_extractor`` agent which the Python port did not reimplement; it
# stays in ``FLAT_ONLY_SPANS`` until that agent is ported.
# ``SPAN_INVENTORY_NARRATOR_EXTRACTED`` IS live — it fires from the
# items_gained/items_lost block of ``server/narration_apply.py`` (the
# integrated narrator pipeline replaces the standalone extractor agent in
# the port).
# ---------------------------------------------------------------------------
SPAN_INVENTORY_EXTRACTION = "inventory.extraction"
SPAN_INVENTORY_NARRATOR_EXTRACTED = "inventory.narrator_extracted"
SPAN_ROUTES[SPAN_INVENTORY_NARRATOR_EXTRACTED] = SpanRoute(
    event_type="state_transition",
    component="inventory",
    extract=lambda span: {
        "field": "inventory",
        "op": "narrator_extracted",
        "gained": (span.attributes or {}).get("gained_json", "[]"),
        "lost": (span.attributes or {}).get("lost_json", "[]"),
        "gained_count": (span.attributes or {}).get("gained_count", 0),
        "lost_count": (span.attributes or {}).get("lost_count", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

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
# Lore — sidequest-server/narration_apply.py
#
# Python-port note (ADR-082): lore-establishment is a Python-port emission
# path — the narrator extracts canonical lore statements from its turn
# response (``NarrationTurnResult.lore_established``) and
# ``narration_apply.py`` appends previously-unseen entries to
# ``snapshot.lore_established``. The route emits the ``lore_retrieval``
# typed event with ``component=lore`` so the GM panel's Lore tab shows
# narrator-driven additions alongside the existing
# character-creation-seed and retrieval-failure entries.
# ---------------------------------------------------------------------------
SPAN_LORE_ESTABLISHED = "lore.established"
SPAN_ROUTES[SPAN_LORE_ESTABLISHED] = SpanRoute(
    event_type="lore_retrieval",
    component="lore",
    extract=lambda span: {
        "field": "lore_established",
        "op": "appended",
        "reason": "narrator_established",
        "items": (span.attributes or {}).get("items_json", "[]"),
        "added_count": (span.attributes or {}).get("added_count", 0),
        "total": (span.attributes or {}).get("total", 0),
        "player_name": (span.attributes or {}).get("player_name", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

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
# Lobby — sidequest-server/rest.py
# Emitted when the lobby's force_new flag triggers slug disambiguation,
# so the GM panel can see the rename rather than wonder why the typed
# name suddenly maps to "<slug>-2".
# ---------------------------------------------------------------------------
SPAN_LOBBY_FORCE_NEW_DISAMBIGUATED = "lobby.force_new_disambiguated"
# Emitted when MP-mode POST /api/games returns an existing same-slug MP
# game instead of allocating ``-N`` (playtest 2026-04-26 S4-UX). The
# UI's ``force_new`` flag is meaningful for solo (per-player journeys)
# but actively wrong for MP: cross-host lobbies have no shared local
# history, so without this short-circuit P2 always splits the table.
SPAN_LOBBY_SESSION_JOIN_EXISTING = "lobby.session_join_existing"

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
    *,
    turn_id: int,
    player_id: str,
    agent_name: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open the root `turn` span for a dispatch.

    Every other span opened during this dispatch becomes a child of this
    span. Without it, traces are orphaned — the Timing tab cannot group by
    turn and the Subsystems tab cannot derive per-turn exercise summaries.

    Required attributes match ADR-031 §"Layer 2" turn-root contract:
    turn_id, player_id, agent_name. Extras are accepted via **attrs and
    set on the span verbatim.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(SPAN_TURN) as span:
        span.set_attribute("turn_id", turn_id)
        span.set_attribute("player_id", player_id)
        span.set_attribute("agent_name", agent_name)
        for k, v in attrs.items():
            span.set_attribute(k, v)
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


@contextmanager
def lobby_force_new_disambiguated_span(
    requested_slug: str,
    final_slug: str,
    attempts: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOBBY_FORCE_NEW_DISAMBIGUATED.

    Emitted when POST /api/games receives ``force_new=True`` and the
    naturally-derived slug already exists, so the server appends a numeric
    counter to mint a fresh slug. Surfacing the rename keeps the GM panel
    honest — without it the lobby would silently route a "new" journey to
    a slug the user never asked for.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOBBY_FORCE_NEW_DISAMBIGUATED,
        attributes={
            "requested_slug": requested_slug,
            "final_slug": final_slug,
            "attempts": attempts,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def lobby_session_join_existing_span(
    slug: str,
    mode: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Context manager wrapping SPAN_LOBBY_SESSION_JOIN_EXISTING.

    Emitted when POST /api/games short-circuits the ``force_new`` path in
    MP mode because an existing same-slug MP game is already on disk —
    the lobby's per-browser ``force_new`` heuristic cannot see other
    players' sessions, so P2 always sends ``force_new=True`` and would
    otherwise be routed to ``<slug>-2`` (playtest 2026-04-26 S4-UX).
    Surfacing the join lets the GM panel verify P2 actually landed in
    P1's table rather than divining from log silence.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_LOBBY_SESSION_JOIN_EXISTING,
        attributes={
            "slug": slug,
            "mode": mode,
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


# ---------------------------------------------------------------------------
# Encounter (dual-track momentum, spec 2026-04-25)
# ---------------------------------------------------------------------------
SPAN_ENCOUNTER_BEAT_SKIPPED = "encounter.beat_skipped"
SPAN_ENCOUNTER_INVALID_SIDE = "encounter.invalid_side"
SPAN_ENCOUNTER_INVALID_OUTCOME_TIER = "encounter.invalid_outcome_tier"
SPAN_ENCOUNTER_METRIC_ADVANCE = "encounter.metric_advance"
SPAN_ENCOUNTER_TAG_CREATED = "encounter.tag_created"
SPAN_ENCOUNTER_TAG_BACKFIRE = "encounter.tag_backfire"
SPAN_ENCOUNTER_STATUS_ADDED = "encounter.status_added"
SPAN_ENCOUNTER_STATUS_CLEARED = "encounter.status_cleared"
SPAN_ENCOUNTER_YIELD_RECEIVED = "encounter.yield_received"
SPAN_ENCOUNTER_YIELD_RESOLVED = "encounter.yield_resolved"
SPAN_ENCOUNTER_RESOLUTION_SIGNAL_EMITTED = "encounter.resolution_signal_emitted"
SPAN_ENCOUNTER_RESOLUTION_SIGNAL_CONSUMED = "encounter.resolution_signal_consumed"


# ---------------------------------------------------------------------------
# Dogfight sealed-letter resolution — sidequest-server/dispatch/sealed_letter.py
#
# Three spans bracket the simultaneous-commit lookup pipeline so the GM
# panel can prove the engine ran (vs. the narrator improvising):
#   1. confrontation_started — handler entry, names both actors
#   2. maneuver_committed    — fires once per actor with the chosen maneuver
#   3. cell_resolved         — fires after lookup with cell name + shape
#
# Routed to typed ``state_transition`` events with ``component="dogfight"``
# so the GM panel's Subsystems tab gets a per-encounter timeline (T4).
# The flat ``agent_span_close`` firehose still carries the same data for
# the Console / Timing tabs — the typed event is additive.
#
# Dogfight subsystem — port-MVP scope.
#
# ADR-077 prescribes 7 spans total. This port ships the 3 below;
# the remaining 4 are deferred along with their gating subsystems:
#
#   - SPAN_DOGFIGHT_GUN_SOLUTION_FIRED — needs explicit fire-event lifecycle
#   - SPAN_DOGFIGHT_ENERGY_DEPLETED — needs energy pool subsystem
#   - SPAN_DOGFIGHT_SKILL_TIER_RESOLVED — needs pilot skill tier loading
#   - SPAN_DOGFIGHT_ACE_INSTINCT_USED — needs tier-3 ace instinct mechanic
#
# All four are tracked as post-MVP work; the 3 below give the GM panel
# enough signal to verify the engine engaged without claiming features
# that don't exist yet.
# ---------------------------------------------------------------------------
SPAN_DOGFIGHT_CONFRONTATION_STARTED = "dogfight.confrontation_started"
SPAN_ROUTES[SPAN_DOGFIGHT_CONFRONTATION_STARTED] = SpanRoute(
    event_type="state_transition",
    component="dogfight",
    extract=lambda span: {
        "field": "dogfight",
        "op": "confrontation_started",
        "encounter_type": (span.attributes or {}).get("encounter_type", ""),
        "red_actor": (span.attributes or {}).get("red_actor", ""),
        "blue_actor": (span.attributes or {}).get("blue_actor", ""),
    },
)
SPAN_DOGFIGHT_MANEUVER_COMMITTED = "dogfight.maneuver_committed"
SPAN_ROUTES[SPAN_DOGFIGHT_MANEUVER_COMMITTED] = SpanRoute(
    event_type="state_transition",
    component="dogfight",
    extract=lambda span: {
        "field": "dogfight",
        "op": "maneuver_committed",
        "actor": (span.attributes or {}).get("actor", ""),
        "maneuver": (span.attributes or {}).get("maneuver", ""),
        "role": (span.attributes or {}).get("role", ""),
    },
)
SPAN_DOGFIGHT_CELL_RESOLVED = "dogfight.cell_resolved"
SPAN_ROUTES[SPAN_DOGFIGHT_CELL_RESOLVED] = SpanRoute(
    event_type="state_transition",
    component="dogfight",
    extract=lambda span: {
        "field": "dogfight",
        "op": "cell_resolved",
        "cell_name": (span.attributes or {}).get("cell_name", ""),
        "shape": (span.attributes or {}).get("shape", ""),
        "red_maneuver": (span.attributes or {}).get("red_maneuver", ""),
        "blue_maneuver": (span.attributes or {}).get("blue_maneuver", ""),
        "extend_and_return_triggered": (span.attributes or {}).get(
            "extend_and_return_triggered", False,
        ),
    },
)


@contextmanager
def dogfight_confrontation_started_span(
    *,
    encounter_type: str,
    red_actor: str,
    blue_actor: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap the sealed-letter handler entry. Names both pilots so the GM
    panel can correlate the resolution with the active encounter actors.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_DOGFIGHT_CONFRONTATION_STARTED,
        attributes={
            "encounter_type": encounter_type,
            "red_actor": red_actor,
            "blue_actor": blue_actor,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def dogfight_maneuver_committed_span(
    *,
    actor: str,
    maneuver: str,
    role: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap a single committed maneuver. Fires twice per resolution turn
    (once for red, once for blue) so the timeline shows both commits.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_DOGFIGHT_MANEUVER_COMMITTED,
        attributes={
            "actor": actor,
            "maneuver": maneuver,
            "role": role,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def dogfight_cell_resolved_span(
    *,
    cell_name: str,
    shape: str,
    red_maneuver: str,
    blue_maneuver: str,
    extend_and_return_triggered: bool,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap the post-lookup span. Carries the matched cell name, the
    cell ``shape`` (passive/offense/evasive descriptor authored in the
    interaction table), and whether the extend-and-return rule fired.
    """
    t = _tracer if _tracer is not None else tracer()
    with t.start_as_current_span(
        SPAN_DOGFIGHT_CELL_RESOLVED,
        attributes={
            "cell_name": cell_name,
            "shape": shape,
            "red_maneuver": red_maneuver,
            "blue_maneuver": blue_maneuver,
            "extend_and_return_triggered": extend_and_return_triggered,
            **attrs,
        },
    ) as span:
        yield span


@contextmanager
def encounter_beat_skipped_span(
    *, reason: str, actor: str, actor_side: str, beat_id: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_BEAT_SKIPPED,
        attributes={"reason": reason, "actor": actor,
                    "actor_side": actor_side, "beat_id": beat_id, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_invalid_side_span(
    *, actor_name: str, declared_side: str, valid_set: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_INVALID_SIDE,
        attributes={"actor_name": actor_name, "declared_side": declared_side,
                    "valid_set": valid_set, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_invalid_outcome_tier_span(
    *, beat_id: str, actor: str, declared_tier: str, valid_set: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_INVALID_OUTCOME_TIER,
        attributes={"beat_id": beat_id, "actor": actor,
                    "declared_tier": declared_tier, "valid_set": valid_set, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_metric_advance_span(
    *, side: str, delta_kind: str, delta: int, before: int, after: int, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_METRIC_ADVANCE,
        attributes={"side": side, "delta_kind": delta_kind, "delta": delta,
                    "before": before, "after": after, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_tag_created_span(
    *, tag_text: str, created_by: str, target: str | None,
    leverage: int, fleeting: bool, created_via: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_TAG_CREATED,
        attributes={"tag_text": tag_text, "created_by": created_by,
                    "target": target or "", "leverage": leverage,
                    "fleeting": fleeting, "created_via": created_via, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_tag_backfire_span(
    *, tag_text: str, created_by: str, target: str, triggering_beat: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_TAG_BACKFIRE,
        attributes={"tag_text": tag_text, "created_by": created_by,
                    "target": target, "triggering_beat": triggering_beat, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_status_added_span(
    *, actor: str, text: str, severity: str, source: str, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_STATUS_ADDED,
        attributes={"actor": actor, "text": text, "severity": severity,
                    "source": source, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_status_cleared_span(
    *, actor: str, text: str, severity: str, reason: str, **attrs: Any,
) -> Iterator[trace.Span]:
    """Fires when a Status leaves a CreatureCore.statuses list.

    ``reason`` is one of:
      - ``"scene_end"``: Scratch auto-clear when an encounter resolves.
      - ``"narrator_clear"``: explicit clear emitted in status_changes.
      - ``"location_change"``: scene transition swept stale Scratches.

    Without this span the GM panel can't tell whether a condition fell off
    because of a real subsystem decision or because the narrator simply
    stopped mentioning it. Per CLAUDE.md OTEL Observability Principle.
    """
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_STATUS_CLEARED,
        attributes={"actor": actor, "text": text, "severity": severity,
                    "reason": reason, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_yield_received_span(
    *, player_id: str, actor_name: str, prior_player_metric: int,
    prior_opponent_metric: int, statuses_taken_this_encounter: int, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_YIELD_RECEIVED,
        attributes={"player_id": player_id, "actor_name": actor_name,
                    "prior_player_metric": prior_player_metric,
                    "prior_opponent_metric": prior_opponent_metric,
                    "statuses_taken_this_encounter": statuses_taken_this_encounter,
                    **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_yield_resolved_span(
    *, outcome: str, yielded_actors: tuple[str, ...], edge_refreshed: int, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_YIELD_RESOLVED,
        attributes={"outcome": outcome,
                    "yielded_actors": ",".join(yielded_actors),
                    "edge_refreshed": edge_refreshed, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_resolution_signal_emitted_span(
    *, outcome: str, final_player_metric: int, final_opponent_metric: int, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_RESOLUTION_SIGNAL_EMITTED,
        attributes={"outcome": outcome,
                    "final_player_metric": final_player_metric,
                    "final_opponent_metric": final_opponent_metric, **attrs},
    ) as s:
        yield s


@contextmanager
def encounter_resolution_signal_consumed_span(
    *, outcome: str, final_player_metric: int, final_opponent_metric: int, **attrs: Any,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        SPAN_ENCOUNTER_RESOLUTION_SIGNAL_CONSUMED,
        attributes={"outcome": outcome,
                    "final_player_metric": final_player_metric,
                    "final_opponent_metric": final_opponent_metric, **attrs},
    ) as s:
        yield s


@contextmanager
def npc_auto_registered_span(
    *,
    npc_name: str,
    pronouns: str,
    role: str,
    turn_number: int,
    registry_len: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap an NPC auto-registration. Replaces the direct
    ``publish_event("state_transition", ..., component="npc_registry",
    op="auto_registered")`` call from ``server/narration_apply.py``.

    Attribute key ``npc_name`` (not ``name``) is used to avoid colliding
    with the OTEL span ``name`` reserved attribute.
    """
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "pronouns": pronouns,
        "role": role,
        "turn_number": turn_number,
        "registry_len": registry_len,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_NPC_AUTO_REGISTERED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def npc_reinvented_span(
    *,
    npc_name: str,
    drift_field: str,
    expected: str,
    narrator: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap an NPC identity-drift warning (narrator disagrees with the
    canonical registry entry). Replaces the prior direct
    ``publish_event(..., severity="warning")`` from
    ``server/session_helpers._detect_npc_identity_drift``.

    Sets ``severity="warning"`` as a span attribute — the
    ``WatcherSpanProcessor`` propagates that into the typed event so the
    GM panel renders this as a drift alert, not an info-level notice.
    """
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "drift_field": drift_field,
        "expected": expected,
        "narrator": narrator,
        "turn_number": turn_number,
        "severity": "warning",
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_NPC_REINVENTED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def quest_update_span(
    *,
    updates: dict[str, str],
    player_name: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap a quest_log mutation block. Replaces the direct
    ``publish_event("state_transition", ..., component="quest_log")``
    that ``server/narration_apply.py`` used pre-Phase-2 — the route
    extracts the same fields, so the dashboard sees no payload change.

    ``updates`` is serialized as a JSON string so it survives the OTEL
    attribute primitive-types restriction (dict/list values silently drop).
    """
    import json as _json

    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "updates_json": _json.dumps(dict(updates), sort_keys=True),
        "updates_count": len(updates),
        "player_name": player_name,
        "turn_number": turn_number,
        **attrs,
    }
    with t.start_as_current_span(SPAN_QUEST_UPDATE, attributes=attributes) as span:
        yield span


@contextmanager
def inventory_narrator_extracted_span(
    *,
    gained: list[str],
    lost: list[str],
    player_name: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap a narrator-extracted inventory mutation block. Replaces the
    direct ``publish_event("state_transition", ..., component="inventory",
    op="narrator_extracted")`` that ``server/narration_apply.py`` used
    pre-Phase-2 — the route preserves the dashboard payload shape (the
    validator's ``inventory_check`` already correlates on these fields).

    ``gained`` / ``lost`` are serialized as JSON strings so OTEL doesn't
    drop the list attributes (the primitive-types restriction silently
    discards dict/list values).
    """
    import json as _json

    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "gained_json": _json.dumps(list(gained)),
        "lost_json": _json.dumps(list(lost)),
        "gained_count": len(gained),
        "lost_count": len(lost),
        "player_name": player_name,
        "turn_number": turn_number,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_INVENTORY_NARRATOR_EXTRACTED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def audio_backend_enabled_span(
    *,
    genre: str,
    mood_count: int,
    sfx_count: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap the audio-backend init success path. Replaces the direct
    ``publish_event("state_transition", ..., component="audio",
    op="enabled")`` from ``server/session_handler.py`` — the route
    preserves the dashboard payload shape.
    """
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "genre": genre,
        "mood_count": mood_count,
        "sfx_count": sfx_count,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_AUDIO_BACKEND_ENABLED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def audio_backend_disabled_span(
    *,
    reason: str,
    genre: str,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap an audio-backend init bail-out (no pack dir, empty config).
    Replaces the direct ``publish_event(..., op="disabled")`` from
    ``server/session_handler.py``. ``reason`` carries the bail cause
    (``pack_dir_missing`` / ``empty_config``).
    """
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "reason": reason,
        "genre": genre,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_AUDIO_BACKEND_DISABLED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def audio_skipped_span(
    *,
    reason: str,
    turn_number: int,
    extra: dict[str, object] | None = None,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap a per-turn audio-cue skip (no audio config, no narration,
    empty cues, dispatch error). Replaces the direct
    ``publish_event(..., op="skipped")`` from
    ``server/session_handler._audio_skip``.

    ``extra`` is JSON-encoded into the span's ``extra_json`` attribute
    because OTEL silently drops dict attribute values; the route
    extract returns the JSON string for dashboard parity with the prior
    flat ``fields.update(extra)`` payload.
    """
    import json as _json

    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "reason": reason,
        "turn_number": turn_number,
        "extra_json": _json.dumps(dict(extra or {}), sort_keys=True),
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_AUDIO_SKIPPED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def audio_dispatched_span(
    *,
    turn_number: int,
    mood: str,
    music_track: str,
    sfx_count: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap a per-turn audio-cue dispatch (mood + music_track + sfx).
    Replaces the direct ``publish_event(..., op="dispatched")`` from
    ``server/session_handler._audio_dispatched``.
    """
    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "turn_number": turn_number,
        "mood": mood,
        "music_track": music_track,
        "sfx_count": sfx_count,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_AUDIO_DISPATCHED, attributes=attributes
    ) as span:
        yield span


@contextmanager
def lore_established_span(
    *,
    items: list[str],
    added_count: int,
    total: int,
    player_name: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wrap the narrator-driven lore-establishment block in
    ``server/narration_apply.py``. The route emits the
    ``lore_retrieval`` typed event with ``component=lore`` and
    ``op=appended`` so the GM panel sees narrator-added canonical lore
    in the same Lore tab as the existing character-creation-seed and
    retrieval-failure entries.

    ``items`` is the list of newly-appended lore strings (i.e. those
    that were not already in ``snapshot.lore_established``). It is
    JSON-encoded into ``items_json`` because OTEL silently drops list
    attribute values; the route extract returns the JSON string so the
    dashboard sees the exact set of strings the narrator just canonised.
    """
    import json as _json

    t = _tracer if _tracer is not None else tracer()
    attributes: dict[str, Any] = {
        "items_json": _json.dumps(list(items)),
        "added_count": added_count,
        "total": total,
        "player_name": player_name,
        "turn_number": turn_number,
        **attrs,
    }
    with t.start_as_current_span(
        SPAN_LORE_ESTABLISHED, attributes=attributes
    ) as span:
        yield span


# ----------------------------------------------------------------------
# Phase 2 deferred — currently-dead spans. Each Phase 2 family rollout
# moves entries OUT of this baseline and into SPAN_ROUTES with proper
# extract lambdas. See ADR-089 + docs/superpowers/specs/2026-04-25-otel-dashboard-restoration-design.md §4.1.
# ----------------------------------------------------------------------
FLAT_ONLY_SPANS.update(
    {
        # Turn pipeline
        SPAN_TURN,  # turn_span() helper added; validator owns turn_complete
        SPAN_TURN_BARRIER,
        SPAN_TURN_STATE_UPDATE,
        SPAN_TURN_SYSTEM_TICK,
        SPAN_TURN_SYSTEM_TICK_TROPES,
        SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT,
        SPAN_TURN_MEDIA,
        SPAN_TURN_TROPES,
        SPAN_TURN_PHASE_TRANSITION,
        SPAN_TURN_SLASH_COMMAND,
        SPAN_TURN_PREPROCESS_LLM,
        SPAN_TURN_PREPROCESS_PARSE,
        SPAN_TURN_PREPROCESS_WISH_CHECK,
        SPAN_TURN_ASSEMBLE,
        # Narrator
        SPAN_NARRATOR_SEALED_ROUND,
        # Orchestrator
        SPAN_ORCHESTRATOR_PROCESS_ACTION,
        SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET,
        SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION,
        SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION,
        SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION,
        SPAN_ORCHESTRATOR_PARTY_PEER_INJECTION,
        SPAN_ORCHESTRATOR_LORE_FILTER,
        # Turn LLM pipeline
        SPAN_TURN_AGENT_LLM_PROMPT_BUILD,
        SPAN_TURN_AGENT_LLM_PARSE_RESPONSE,
        # Content resolution
        SPAN_CONTENT_RESOLVE,
        # Trope engine
        SPAN_TROPE_TICK,
        SPAN_TROPE_TICK_PER,
        SPAN_TROPE_ROOM_TICK,
        SPAN_TROPE_ACTIVATE,
        SPAN_TROPE_RESOLVE,
        SPAN_TROPE_CROSS_SESSION,
        SPAN_TROPE_EVALUATE_TRIGGERS,
        # Barrier
        SPAN_BARRIER_ACTIVATED,
        SPAN_BARRIER_RESOLVED,
        # Music / audio
        SPAN_MUSIC_EVALUATE,
        SPAN_MUSIC_CLASSIFY_MOOD,
        # Persistence
        SPAN_PERSISTENCE_SAVE,
        SPAN_PERSISTENCE_LOAD,
        SPAN_PERSISTENCE_DELETE,
        # Character generation
        SPAN_CHARGEN_STAT_ROLL,
        SPAN_CHARGEN_STATS_GENERATED,
        SPAN_CHARGEN_HP_FORMULA,
        SPAN_CHARGEN_BACKSTORY_COMPOSED,
        # NPC — port-artifact constants kept flat-only.
        # SPAN_NPC_AUTO_REGISTERED and SPAN_NPC_REINVENTED moved to
        # SPAN_ROUTES (NPC bundle, 2026-04-25).
        SPAN_NPC_MERGE_PATCH,
        SPAN_NPC_REGISTRATION,
        # Creature
        SPAN_CREATURE_HP_DELTA,
        # Disposition
        SPAN_DISPOSITION_SHIFT,
        # State patches — port-artifact constants kept flat-only.
        # SPAN_QUEST_UPDATE moved to SPAN_ROUTES (state-patch bundle, 2026-04-25).
        SPAN_APPLY_WORLD_PATCH,
        SPAN_BUILD_PROTOCOL_DELTA,
        # Delta — port-artifact, no production caller (see comment near constant).
        SPAN_COMPUTE_DELTA,
        # Merchant
        SPAN_MERCHANT_CONTEXT_INJECTED,
        SPAN_MERCHANT_TRANSACTION,
        # Inventory
        SPAN_INVENTORY_EXTRACTION,
        # Continuity
        SPAN_CONTINUITY_LLM_VALIDATION,
        # Context builder
        SPAN_COMPOSE,
        # World builder
        SPAN_WORLD_MATERIALIZED,
        # RAG
        SPAN_RAG_PROSE_CLEANUP,
        # Script tool
        SPAN_SCRIPT_TOOL_PROMPT_INJECTED,
        # Reminders
        SPAN_REMINDER_SPAWNED,
        SPAN_REMINDER_FIRED,
        # Pregen
        SPAN_PREGEN_SEED_MANUAL,
        # Catch-up narration
        SPAN_CATCH_UP_GENERATE,
        # Scenario
        SPAN_SCENARIO_ADVANCE,
        SPAN_SCENARIO_ACCUSATION,
        # Monster manual
        SPAN_MONSTER_MANUAL_INJECTED,
        # Multiplayer lifecycle
        SPAN_MP_GAME_CREATED,
        SPAN_MP_SLUG_CONNECT,
        SPAN_MP_SEAT,
        SPAN_MP_PLAYER_ACTION_PAUSED,
        # Lobby
        SPAN_LOBBY_FORCE_NEW_DISAMBIGUATED,
        SPAN_LOBBY_SESSION_JOIN_EXISTING,
        # Encounter (dual-track momentum, spec 2026-04-25) — flat-only baseline.
        # Routing decisions land with the GM panel encounter timeline rollout.
        SPAN_ENCOUNTER_BEAT_SKIPPED,
        SPAN_ENCOUNTER_INVALID_SIDE,
        SPAN_ENCOUNTER_INVALID_OUTCOME_TIER,
        SPAN_ENCOUNTER_METRIC_ADVANCE,
        SPAN_ENCOUNTER_TAG_CREATED,
        SPAN_ENCOUNTER_TAG_BACKFIRE,
        SPAN_ENCOUNTER_STATUS_ADDED,
        SPAN_ENCOUNTER_STATUS_CLEARED,
        SPAN_ENCOUNTER_YIELD_RECEIVED,
        SPAN_ENCOUNTER_YIELD_RESOLVED,
        SPAN_ENCOUNTER_RESOLUTION_SIGNAL_EMITTED,
        SPAN_ENCOUNTER_RESOLUTION_SIGNAL_CONSUMED,
    }
)
