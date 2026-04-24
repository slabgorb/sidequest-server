"""Rust-parity test for combat.* / encounter.* span names.

Story 3.4 AC: OTEL span names are byte-identical to Rust. GM-panel queries
break on drift (docs/plans/phase-3-combat-port.md Risks §2).
"""
from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


def test_combat_encounter_span_constants_match_rust_names() -> None:
    from sidequest.telemetry.spans import (
        SPAN_COMBAT_ENDED,
        SPAN_COMBAT_PLAYER_DEAD,
        SPAN_COMBAT_TICK,
        SPAN_ENCOUNTER_BEAT_APPLIED,
        SPAN_ENCOUNTER_CONFRONTATION_INITIATED,
        SPAN_ENCOUNTER_EMPTY_ACTOR_LIST,
        SPAN_ENCOUNTER_PHASE_TRANSITION,
        SPAN_ENCOUNTER_RESOLVED,
    )
    assert SPAN_COMBAT_TICK == "combat.tick"
    assert SPAN_COMBAT_ENDED == "combat.ended"
    assert SPAN_COMBAT_PLAYER_DEAD == "combat.player_dead"
    assert SPAN_ENCOUNTER_PHASE_TRANSITION == "encounter.phase_transition"
    assert SPAN_ENCOUNTER_RESOLVED == "encounter.resolved"
    assert SPAN_ENCOUNTER_BEAT_APPLIED == "encounter.beat_applied"
    assert SPAN_ENCOUNTER_CONFRONTATION_INITIATED == (
        "encounter.confrontation_initiated"
    )
    assert SPAN_ENCOUNTER_EMPTY_ACTOR_LIST == "encounter.empty_actor_list"


def test_encounter_empty_actor_list_emits_attrs() -> None:
    from sidequest.telemetry.spans import encounter_empty_actor_list_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_empty_actor_list_span(
        _tracer=tracer,
        encounter_type="combat",
        genre_slug="mutant_wasteland",
        player_name="Slabgorb",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "encounter.empty_actor_list"
    assert span.attributes["encounter_type"] == "combat"
    assert span.attributes["genre_slug"] == "mutant_wasteland"
    assert span.attributes["player_name"] == "Slabgorb"


def test_combat_tick_span_emits_attributes() -> None:
    from sidequest.telemetry.spans import combat_tick_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with combat_tick_span(
        _tracer=tracer, encounter_type="combat", beat=3, phase="Escalation",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "combat.tick"
    assert span.attributes["encounter_type"] == "combat"
    assert span.attributes["beat"] == 3
    assert span.attributes["phase"] == "Escalation"


def test_encounter_phase_transition_span_emits_from_to() -> None:
    from sidequest.telemetry.spans import encounter_phase_transition_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_phase_transition_span(
        _tracer=tracer, from_phase="Opening", to_phase="Escalation",
        encounter_type="combat",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "encounter.phase_transition"
    assert span.attributes["from"] == "Opening"
    assert span.attributes["to"] == "Escalation"
    assert span.attributes["encounter_type"] == "combat"


def test_encounter_confrontation_initiated_emits_attrs() -> None:
    from sidequest.telemetry.spans import encounter_confrontation_initiated_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_confrontation_initiated_span(
        _tracer=tracer, encounter_type="combat", genre_slug="caverns_and_claudes",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "encounter.confrontation_initiated"
    assert span.attributes["encounter_type"] == "combat"
    assert span.attributes["genre_slug"] == "caverns_and_claudes"


def test_encounter_beat_applied_emits_attrs() -> None:
    from sidequest.telemetry.spans import encounter_beat_applied_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_beat_applied_span(
        _tracer=tracer, encounter_type="combat", actor="Rux",
        beat_id="attack", metric_delta=2,
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "encounter.beat_applied"
    assert span.attributes["actor"] == "Rux"
    assert span.attributes["beat_id"] == "attack"
    assert span.attributes["metric_delta"] == 2


def test_encounter_resolved_emits_attrs_with_source() -> None:
    from sidequest.telemetry.spans import encounter_resolved_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_resolved_span(
        _tracer=tracer, encounter_type="combat", outcome="victory", source="metric",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "encounter.resolved"
    assert span.attributes["encounter_type"] == "combat"
    assert span.attributes["outcome"] == "victory"
    assert span.attributes["source"] == "metric"


def test_combat_ended_emits_outcome_and_duration() -> None:
    from sidequest.telemetry.spans import combat_ended_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with combat_ended_span(_tracer=tracer, outcome="victory", duration_beats=5):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "combat.ended"
    assert span.attributes["outcome"] == "victory"
    assert span.attributes["duration_beats"] == 5


def test_combat_player_dead_emits_player_name() -> None:
    from sidequest.telemetry.spans import combat_player_dead_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with combat_player_dead_span(_tracer=tracer, player_name="Rux"):
        pass
    [span] = exporter.get_finished_spans()
    assert span.name == "combat.player_dead"
    assert span.attributes["player_name"] == "Rux"


def test_encounter_resolved_omits_outcome_when_none() -> None:
    """When ``outcome`` is None, the attribute is absent from the span.

    GM-panel queries filter on outcome=victory / outcome=defeat etc.; a
    sentinel like "unknown" would pollute those queries. Absence is the
    contract for resolution paths that don't have a named outcome yet.
    """
    from sidequest.telemetry.spans import encounter_resolved_span

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with encounter_resolved_span(
        _tracer=tracer, encounter_type="combat", outcome=None, source="player_death",
    ):
        pass
    [span] = exporter.get_finished_spans()
    assert "outcome" not in span.attributes
    assert span.attributes["source"] == "player_death"
    assert span.attributes["encounter_type"] == "combat"
