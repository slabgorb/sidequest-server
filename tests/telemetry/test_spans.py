"""Tests for the OTEL span catalog — sidequest/telemetry/spans.py.

Covers:
- Every span constant equals its Rust literal (regression gate)
- Helper context managers emit spans with correct names and key attributes
- Smoke test via InMemorySpanExporter proves spans are actually exported
"""

from __future__ import annotations

import pytest

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Return a TracerProvider backed by an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _local_tracer(provider: TracerProvider) -> trace.Tracer:
    """Get a tracer scoped to a specific provider (avoids global provider lock)."""
    return provider.get_tracer("test")


# ---------------------------------------------------------------------------
# Span constant correctness — one assertion per Rust literal
# ---------------------------------------------------------------------------


def test_turn_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_TURN,
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
    )
    assert SPAN_TURN == "turn"
    assert SPAN_TURN_BARRIER == "turn.barrier"
    assert SPAN_TURN_STATE_UPDATE == "turn.state_update"
    assert SPAN_TURN_SYSTEM_TICK == "turn.system_tick"
    assert SPAN_TURN_SYSTEM_TICK_TROPES == "turn.system_tick.tropes"
    assert SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT == "turn.system_tick.beat_context"
    assert SPAN_TURN_MEDIA == "turn.media"
    assert SPAN_TURN_TROPES == "turn.tropes"
    assert SPAN_TURN_PHASE_TRANSITION == "turn.phase_transition"
    assert SPAN_TURN_SLASH_COMMAND == "turn.slash_command"
    assert SPAN_TURN_PREPROCESS_LLM == "turn.preprocess.llm"
    assert SPAN_TURN_PREPROCESS_PARSE == "turn.preprocess.parse"
    assert SPAN_TURN_PREPROCESS_WISH_CHECK == "turn.preprocess.wish_check"
    assert SPAN_TURN_ASSEMBLE == "turn.assemble"


def test_narrator_span_names() -> None:
    from sidequest.telemetry.spans import SPAN_NARRATOR_SEALED_ROUND
    assert SPAN_NARRATOR_SEALED_ROUND == "narrator.sealed_round"


def test_orchestrator_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_ORCHESTRATOR_PROCESS_ACTION,
        SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET,
        SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION,
        SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION,
        SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION,
        SPAN_ORCHESTRATOR_LORE_FILTER,
    )
    assert SPAN_ORCHESTRATOR_PROCESS_ACTION == "orchestrator.process_action"
    assert SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET == "orchestrator.narrator_session_reset"
    assert SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION == "orchestrator.genre_identity_injection"
    assert SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION == "orchestrator.tactical_grid_injection"
    assert SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION == "orchestrator.trope_beat_injection"
    assert SPAN_ORCHESTRATOR_LORE_FILTER == "orchestrator.lore_filter"


def test_agent_call_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_AGENT_CALL,
        SPAN_AGENT_CALL_SESSION,
    )
    assert SPAN_AGENT_CALL == "agent.call"
    assert SPAN_AGENT_CALL_SESSION == "agent.call.session"


def test_turn_agent_llm_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_TURN_AGENT_LLM_PROMPT_BUILD,
        SPAN_TURN_AGENT_LLM_INFERENCE,
        SPAN_TURN_AGENT_LLM_PARSE_RESPONSE,
    )
    assert SPAN_TURN_AGENT_LLM_PROMPT_BUILD == "turn.agent_llm.prompt_build"
    assert SPAN_TURN_AGENT_LLM_INFERENCE == "turn.agent_llm.inference"
    assert SPAN_TURN_AGENT_LLM_PARSE_RESPONSE == "turn.agent_llm.parse_response"


def test_content_span_names() -> None:
    from sidequest.telemetry.spans import SPAN_CONTENT_RESOLVE
    assert SPAN_CONTENT_RESOLVE == "content.resolve"


def test_trope_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_TROPE_TICK,
        SPAN_TROPE_TICK_PER,
        SPAN_TROPE_ROOM_TICK,
        SPAN_TROPE_ACTIVATE,
        SPAN_TROPE_RESOLVE,
        SPAN_TROPE_CROSS_SESSION,
        SPAN_TROPE_EVALUATE_TRIGGERS,
    )
    assert SPAN_TROPE_TICK == "trope_tick"
    assert SPAN_TROPE_TICK_PER == "trope.tick"
    assert SPAN_TROPE_ROOM_TICK == "trope.room_tick"
    assert SPAN_TROPE_ACTIVATE == "trope_activate"
    assert SPAN_TROPE_RESOLVE == "trope_resolve"
    assert SPAN_TROPE_CROSS_SESSION == "trope.cross_session"
    assert SPAN_TROPE_EVALUATE_TRIGGERS == "trope.evaluate_triggers"


def test_barrier_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_BARRIER_ACTIVATED,
        SPAN_BARRIER_RESOLVED,
    )
    assert SPAN_BARRIER_ACTIVATED == "barrier.activated"
    assert SPAN_BARRIER_RESOLVED == "barrier.resolved"


def test_music_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_MUSIC_EVALUATE,
        SPAN_MUSIC_CLASSIFY_MOOD,
    )
    assert SPAN_MUSIC_EVALUATE == "music_evaluate"
    assert SPAN_MUSIC_CLASSIFY_MOOD == "music_classify_mood"


def test_persistence_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_PERSISTENCE_SAVE,
        SPAN_PERSISTENCE_LOAD,
        SPAN_PERSISTENCE_DELETE,
    )
    assert SPAN_PERSISTENCE_SAVE == "persistence_save"
    assert SPAN_PERSISTENCE_LOAD == "persistence_load"
    assert SPAN_PERSISTENCE_DELETE == "persistence_delete"


def test_chargen_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_CHARGEN_STAT_ROLL,
        SPAN_CHARGEN_STATS_GENERATED,
        SPAN_CHARGEN_HP_FORMULA,
        SPAN_CHARGEN_BACKSTORY_COMPOSED,
    )
    assert SPAN_CHARGEN_STAT_ROLL == "chargen.stat_roll"
    assert SPAN_CHARGEN_STATS_GENERATED == "chargen.stats_generated"
    assert SPAN_CHARGEN_HP_FORMULA == "chargen.hp_formula"
    assert SPAN_CHARGEN_BACKSTORY_COMPOSED == "chargen.backstory_composed"


def test_npc_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_NPC_MERGE_PATCH,
        SPAN_NPC_REGISTRATION,
    )
    assert SPAN_NPC_MERGE_PATCH == "npc_merge_patch"
    assert SPAN_NPC_REGISTRATION == "npc.registration"


def test_creature_span_names() -> None:
    from sidequest.telemetry.spans import SPAN_CREATURE_HP_DELTA
    assert SPAN_CREATURE_HP_DELTA == "creature.hp_delta"


def test_disposition_span_names() -> None:
    from sidequest.telemetry.spans import SPAN_DISPOSITION_SHIFT
    assert SPAN_DISPOSITION_SHIFT == "disposition.shift"


def test_state_patch_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_APPLY_WORLD_PATCH,
        SPAN_QUEST_UPDATE,
        SPAN_BUILD_PROTOCOL_DELTA,
        SPAN_COMPUTE_DELTA,
    )
    assert SPAN_APPLY_WORLD_PATCH == "apply_world_patch"
    assert SPAN_QUEST_UPDATE == "quest_update"
    assert SPAN_BUILD_PROTOCOL_DELTA == "build_protocol_delta"
    assert SPAN_COMPUTE_DELTA == "compute_delta"


def test_merchant_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_MERCHANT_CONTEXT_INJECTED,
        SPAN_MERCHANT_TRANSACTION,
    )
    assert SPAN_MERCHANT_CONTEXT_INJECTED == "merchant.context_injected"
    assert SPAN_MERCHANT_TRANSACTION == "merchant.transaction"


def test_misc_agent_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_INVENTORY_EXTRACTION,
        SPAN_CONTINUITY_LLM_VALIDATION,
        SPAN_COMPOSE,
        SPAN_WORLD_MATERIALIZED,
        SPAN_RAG_PROSE_CLEANUP,
        SPAN_SCRIPT_TOOL_PROMPT_INJECTED,
    )
    assert SPAN_INVENTORY_EXTRACTION == "inventory.extraction"
    assert SPAN_CONTINUITY_LLM_VALIDATION == "continuity.llm_validation"
    assert SPAN_COMPOSE == "compose"
    assert SPAN_WORLD_MATERIALIZED == "world.materialized"
    assert SPAN_RAG_PROSE_CLEANUP == "rag.prose_cleanup"
    assert SPAN_SCRIPT_TOOL_PROMPT_INJECTED == "script_tool.prompt_injected"


def test_server_misc_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_REMINDER_SPAWNED,
        SPAN_REMINDER_FIRED,
        SPAN_PREGEN_SEED_MANUAL,
        SPAN_CATCH_UP_GENERATE,
        SPAN_SCENARIO_ADVANCE,
        SPAN_SCENARIO_ACCUSATION,
        SPAN_MONSTER_MANUAL_INJECTED,
    )
    assert SPAN_REMINDER_SPAWNED == "reminder_spawned"
    assert SPAN_REMINDER_FIRED == "reminder_fired"
    assert SPAN_PREGEN_SEED_MANUAL == "pregen.seed_manual"
    assert SPAN_CATCH_UP_GENERATE == "catch_up.generate"
    assert SPAN_SCENARIO_ADVANCE == "scenario.advance"
    assert SPAN_SCENARIO_ACCUSATION == "scenario.accusation"
    assert SPAN_MONSTER_MANUAL_INJECTED == "monster_manual.injected"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_turn_span_helper_emits_span() -> None:
    """turn_span() starts a span with the correct name."""
    from sidequest.telemetry.spans import turn_span, SPAN_TURN

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with turn_span("player-1", "I look around", _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == SPAN_TURN


def test_turn_span_helper_records_player_id_attribute() -> None:
    """turn_span() records player_id on the span."""
    from sidequest.telemetry.spans import turn_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with turn_span("player-42", "I attack", _tracer=t) as _span:
        pass

    spans = exporter.get_finished_spans()
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("player_id") == "player-42"


def test_turn_span_helper_truncates_action_to_80_chars() -> None:
    """turn_span() truncates the action attribute to 80 chars."""
    from sidequest.telemetry.spans import turn_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    long_action = "a" * 120
    with turn_span("p", long_action, _tracer=t) as _span:
        pass

    spans = exporter.get_finished_spans()
    assert spans[0].attributes is not None
    action_attr = spans[0].attributes.get("action")
    assert action_attr is not None
    assert len(str(action_attr)) == 80


def test_orchestrator_process_action_span_helper() -> None:
    from sidequest.telemetry.spans import orchestrator_process_action_span, SPAN_ORCHESTRATOR_PROCESS_ACTION

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with orchestrator_process_action_span(action_len=42, _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_ORCHESTRATOR_PROCESS_ACTION
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("action_len") == 42


def test_agent_call_span_helper() -> None:
    from sidequest.telemetry.spans import agent_call_span, SPAN_AGENT_CALL

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with agent_call_span(model="claude-opus-4-5", prompt_len=1024, _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_AGENT_CALL
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("model") == "claude-opus-4-5"
    assert spans[0].attributes.get("prompt_len") == 1024


def test_agent_call_session_span_helper() -> None:
    from sidequest.telemetry.spans import agent_call_session_span, SPAN_AGENT_CALL_SESSION

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with agent_call_session_span(model="claude-sonnet-4-5", prompt_len=512, _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_AGENT_CALL_SESSION


def test_content_resolve_span_helper() -> None:
    from sidequest.telemetry.spans import content_resolve_span, SPAN_CONTENT_RESOLVE

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with content_resolve_span(
        axis="archetype",
        field_path="archetypes.rogue",
        genre="caverns_and_claudes",
        world="flickering_reach",
        _tracer=t,
    ) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_CONTENT_RESOLVE
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("content.axis") == "archetype"
    assert spans[0].attributes.get("content.genre") == "caverns_and_claudes"
    assert spans[0].attributes.get("content.world") == "flickering_reach"


def test_content_resolve_span_defaults_world_and_culture() -> None:
    """content_resolve_span() defaults world and culture to empty string."""
    from sidequest.telemetry.spans import content_resolve_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with content_resolve_span(
        axis="audio", field_path="audio.theme", genre="neon_dystopia", _tracer=t
    ):
        pass

    spans = exporter.get_finished_spans()
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("content.world") == ""
    assert spans[0].attributes.get("content.culture") == ""


def test_persistence_save_span_helper() -> None:
    from sidequest.telemetry.spans import persistence_save_span, SPAN_PERSISTENCE_SAVE

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with persistence_save_span("caverns_and_claudes", "flickering_reach", "rux", _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_PERSISTENCE_SAVE
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("genre") == "caverns_and_claudes"
    assert spans[0].attributes.get("player") == "rux"


def test_persistence_load_span_helper() -> None:
    from sidequest.telemetry.spans import persistence_load_span, SPAN_PERSISTENCE_LOAD

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with persistence_load_span("space_opera", "void_current", "axelion", _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_PERSISTENCE_LOAD


def test_trope_tick_span_helper() -> None:
    from sidequest.telemetry.spans import trope_tick_span, SPAN_TROPE_TICK

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with trope_tick_span(trope_count=5, multiplier=1.2, _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_TROPE_TICK
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("trope_count") == 5


def test_turn_agent_llm_inference_span_helper() -> None:
    from sidequest.telemetry.spans import turn_agent_llm_inference_span, SPAN_TURN_AGENT_LLM_INFERENCE

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with turn_agent_llm_inference_span(model="claude-opus-4-5", prompt_len=8192, _tracer=t) as span:
        assert span.is_recording()

    spans = exporter.get_finished_spans()
    assert spans[0].name == SPAN_TURN_AGENT_LLM_INFERENCE
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("model") == "claude-opus-4-5"
    assert spans[0].attributes.get("prompt_len") == 8192


# ---------------------------------------------------------------------------
# Smoke test — in-memory exporter roundtrip
# ---------------------------------------------------------------------------


def test_spans_are_exported_with_correct_name_smoke() -> None:
    """End-to-end: a helper emits a span that is captured by InMemorySpanExporter."""
    from sidequest.telemetry.spans import agent_call_span, SPAN_AGENT_CALL

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with agent_call_span(model="claude-haiku-4-5", prompt_len=256, _tracer=t):
        pass

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    span = finished[0]
    assert span.name == SPAN_AGENT_CALL
    assert span.status.is_ok


def test_multiple_spans_exported_in_order() -> None:
    """Multiple helpers in sequence produce the right spans in order."""
    from sidequest.telemetry.spans import (
        turn_span,
        agent_call_span,
        SPAN_TURN,
        SPAN_AGENT_CALL,
    )

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with turn_span("p1", "go north", _tracer=t):
        pass
    with agent_call_span("claude-opus-4-5", 100, _tracer=t):
        pass

    finished = exporter.get_finished_spans()
    assert len(finished) == 2
    assert finished[0].name == SPAN_TURN
    assert finished[1].name == SPAN_AGENT_CALL


def test_content_resolve_span_extra_attrs_passed_through() -> None:
    """Extra **attrs kwargs are forwarded to the span."""
    from sidequest.telemetry.spans import content_resolve_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)

    with content_resolve_span(
        axis="trope",
        field_path="tropes.bloodhound",
        genre="pulp_noir",
        _tracer=t,
        **{"content.source_tier": "world", "content.elapsed_us": 42},
    ):
        pass

    spans = exporter.get_finished_spans()
    attrs = spans[0].attributes
    assert attrs is not None
    assert attrs.get("content.source_tier") == "world"
    assert attrs.get("content.elapsed_us") == 42


def test_span_name_drift_regression() -> None:
    """Regression gate: all Phase 1 span constants are importable and non-empty strings."""
    from sidequest.telemetry import spans

    phase1_constants = [
        spans.SPAN_TURN,
        spans.SPAN_TURN_BARRIER,
        spans.SPAN_TURN_STATE_UPDATE,
        spans.SPAN_TURN_SYSTEM_TICK,
        spans.SPAN_TURN_SYSTEM_TICK_TROPES,
        spans.SPAN_TURN_SYSTEM_TICK_BEAT_CONTEXT,
        spans.SPAN_TURN_MEDIA,
        spans.SPAN_TURN_TROPES,
        spans.SPAN_TURN_PHASE_TRANSITION,
        spans.SPAN_TURN_SLASH_COMMAND,
        spans.SPAN_TURN_PREPROCESS_LLM,
        spans.SPAN_TURN_PREPROCESS_PARSE,
        spans.SPAN_TURN_PREPROCESS_WISH_CHECK,
        spans.SPAN_TURN_ASSEMBLE,
        spans.SPAN_NARRATOR_SEALED_ROUND,
        spans.SPAN_ORCHESTRATOR_PROCESS_ACTION,
        spans.SPAN_ORCHESTRATOR_NARRATOR_SESSION_RESET,
        spans.SPAN_ORCHESTRATOR_GENRE_IDENTITY_INJECTION,
        spans.SPAN_ORCHESTRATOR_TACTICAL_GRID_INJECTION,
        spans.SPAN_ORCHESTRATOR_TROPE_BEAT_INJECTION,
        spans.SPAN_ORCHESTRATOR_LORE_FILTER,
        spans.SPAN_AGENT_CALL,
        spans.SPAN_AGENT_CALL_SESSION,
        spans.SPAN_TURN_AGENT_LLM_PROMPT_BUILD,
        spans.SPAN_TURN_AGENT_LLM_INFERENCE,
        spans.SPAN_TURN_AGENT_LLM_PARSE_RESPONSE,
        spans.SPAN_CONTENT_RESOLVE,
        spans.SPAN_TROPE_TICK,
        spans.SPAN_TROPE_TICK_PER,
        spans.SPAN_TROPE_ROOM_TICK,
        spans.SPAN_TROPE_ACTIVATE,
        spans.SPAN_TROPE_RESOLVE,
        spans.SPAN_TROPE_CROSS_SESSION,
        spans.SPAN_TROPE_EVALUATE_TRIGGERS,
        spans.SPAN_BARRIER_ACTIVATED,
        spans.SPAN_BARRIER_RESOLVED,
        spans.SPAN_MUSIC_EVALUATE,
        spans.SPAN_MUSIC_CLASSIFY_MOOD,
        spans.SPAN_PERSISTENCE_SAVE,
        spans.SPAN_PERSISTENCE_LOAD,
        spans.SPAN_PERSISTENCE_DELETE,
        spans.SPAN_CHARGEN_STAT_ROLL,
        spans.SPAN_CHARGEN_STATS_GENERATED,
        spans.SPAN_CHARGEN_HP_FORMULA,
        spans.SPAN_CHARGEN_BACKSTORY_COMPOSED,
        spans.SPAN_NPC_MERGE_PATCH,
        spans.SPAN_NPC_REGISTRATION,
        spans.SPAN_CREATURE_HP_DELTA,
        spans.SPAN_DISPOSITION_SHIFT,
        spans.SPAN_APPLY_WORLD_PATCH,
        spans.SPAN_QUEST_UPDATE,
        spans.SPAN_BUILD_PROTOCOL_DELTA,
        spans.SPAN_COMPUTE_DELTA,
        spans.SPAN_MERCHANT_CONTEXT_INJECTED,
        spans.SPAN_MERCHANT_TRANSACTION,
        spans.SPAN_INVENTORY_EXTRACTION,
        spans.SPAN_CONTINUITY_LLM_VALIDATION,
        spans.SPAN_COMPOSE,
        spans.SPAN_WORLD_MATERIALIZED,
        spans.SPAN_RAG_PROSE_CLEANUP,
        spans.SPAN_SCRIPT_TOOL_PROMPT_INJECTED,
        spans.SPAN_REMINDER_SPAWNED,
        spans.SPAN_REMINDER_FIRED,
        spans.SPAN_PREGEN_SEED_MANUAL,
        spans.SPAN_CATCH_UP_GENERATE,
        spans.SPAN_SCENARIO_ADVANCE,
        spans.SPAN_SCENARIO_ACCUSATION,
        spans.SPAN_MONSTER_MANUAL_INJECTED,
    ]

    for constant in phase1_constants:
        assert isinstance(constant, str), f"span constant {constant!r} is not a string"
        assert len(constant) > 0, "span constant must not be empty"


# ---------------------------------------------------------------------------
# Multiplayer span helpers — emission + attribute wiring
# ---------------------------------------------------------------------------


def test_mp_span_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_MP_GAME_CREATED,
        SPAN_MP_PLAYER_ACTION_PAUSED,
        SPAN_MP_SEAT,
        SPAN_MP_SLUG_CONNECT,
    )
    assert SPAN_MP_GAME_CREATED == "mp.game_created"
    assert SPAN_MP_SLUG_CONNECT == "mp.slug_connect"
    assert SPAN_MP_SEAT == "mp.seat"
    assert SPAN_MP_PLAYER_ACTION_PAUSED == "mp.player_action_paused"


def test_mp_game_created_span_emits_attributes() -> None:
    from sidequest.telemetry.spans import mp_game_created_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with mp_game_created_span(
        slug="2026-04-22-grimvault",
        mode="multiplayer",
        genre_slug="caverns_and_claudes",
        world_slug="grimvault",
        resumed=False,
        _tracer=t,
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "mp.game_created"
    assert span.attributes["slug"] == "2026-04-22-grimvault"
    assert span.attributes["mode"] == "multiplayer"
    assert span.attributes["genre_slug"] == "caverns_and_claudes"
    assert span.attributes["world_slug"] == "grimvault"
    assert span.attributes["resumed"] is False


def test_mp_slug_connect_span_carries_pause_resolution() -> None:
    from sidequest.telemetry.spans import mp_slug_connect_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with mp_slug_connect_span(
        slug="2026-04-22-grimvault",
        player_id="alice",
        mode="multiplayer",
        _tracer=t,
    ) as span:
        span.set_attribute("was_paused_before", True)
        span.set_attribute("resolved_pause", True)
    [emitted] = exporter.get_finished_spans()
    assert emitted.name == "mp.slug_connect"
    assert emitted.attributes["slug"] == "2026-04-22-grimvault"
    assert emitted.attributes["player_id"] == "alice"
    assert emitted.attributes["was_paused_before"] is True
    assert emitted.attributes["resolved_pause"] is True


def test_mp_seat_span_handles_none_slot() -> None:
    from sidequest.telemetry.spans import mp_seat_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with mp_seat_span(
        slug="2026-04-22-grimvault",
        player_id="alice",
        character_slot="fighter-01",
        _tracer=t,
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "mp.seat"
    assert span.attributes["character_slot"] == "fighter-01"

    # None character_slot must not crash OTEL (attrs reject None values).
    exporter.clear()
    with mp_seat_span(
        slug="2026-04-22-grimvault",
        player_id="observer",
        character_slot=None,
        _tracer=t,
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.attributes["character_slot"] == ""


def test_mp_player_action_paused_span_emits_absent_list() -> None:
    from sidequest.telemetry.spans import mp_player_action_paused_span

    provider, exporter = _fresh_provider()
    t = _local_tracer(provider)
    with mp_player_action_paused_span(
        slug="2026-04-22-grimvault",
        player_id="alice",
        absent_player_ids=["bob", "carol"],
        _tracer=t,
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "mp.player_action_paused"
    assert span.attributes["absent_count"] == 2
    assert span.attributes["absent_player_ids"] == "bob,carol"
