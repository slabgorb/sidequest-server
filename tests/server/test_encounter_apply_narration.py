from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult, NpcMention
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS, GenreLoader


@pytest.fixture
def cac_snap():
    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load("caverns_and_claudes")
    return snap, pack


@pytest.fixture
def otel_capture():
    """Attach an in-memory exporter to the running TracerProvider.

    Mirrors the otel_capture fixture in test_room_graph_init.py — adds a
    SimpleSpanProcessor alongside the existing processors so span emissions
    from production code fan out to the in-memory sink for assertion.
    """
    from sidequest.telemetry.setup import init_tracer

    init_tracer()  # idempotent
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        f"expected SDK TracerProvider, got {type(provider)!r}"
    )
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def test_narrator_confrontation_trigger_creates_encounter(cac_snap) -> None:
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter is not None
    assert snap.encounter.encounter_type == "combat"


def test_beat_selection_applied_bumps_metric(cac_snap) -> None:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    from sidequest.game.encounter import EncounterMetric, MetricDirection
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="The blade sings.",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.beat == 1
    assert snap.encounter.metric.current == 2


def test_beat_selection_unknown_beat_id_raises(cac_snap) -> None:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    snap.encounter = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="tap_dance", target=None)],
    )
    with pytest.raises(ValueError, match="unknown beat_id"):
        _apply_narration_result_to_snapshot(
            snap, result, player_name="Rux", pack=pack,
        )


def test_metric_crossing_threshold_resolves_encounter(cac_snap) -> None:
    from sidequest.game.encounter import EncounterMetric, MetricDirection, StructuredEncounter
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot
    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=9, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.resolved is True
    assert snap.encounter.structured_phase.value == "Resolution"


def test_ascending_metric_ignores_threshold_low(cac_snap) -> None:
    """Ascending encounters resolve only on threshold_high, never threshold_low.

    Port of Rust encounter.rs direction-aware threshold check. Prevents a chase
    (ascending) from being falsely resolved if the counter dips below zero.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    # Ascending metric sitting below threshold_low — must NOT resolve.
    enc.metric = EncounterMetric(
        name="distance",
        current=-5,
        starting=0,
        direction=MetricDirection.Ascending,
        threshold_high=10,
        threshold_low=0,
    )
    enc.encounter_type = "combat"  # still matched against cac pack
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    # attack metric_delta=2 → -5+2=-3, then clamped to 0 by Ascending rule.
    # Neither threshold_high (10) nor threshold_low (0) triggers resolution
    # on an Ascending metric when only threshold_low-equivalent is crossed.
    assert snap.encounter.resolved is False
    assert snap.encounter.metric.current == 0  # clamped from -3


def test_descending_metric_ignores_threshold_high(cac_snap) -> None:
    """Descending encounters resolve only on threshold_low, never threshold_high."""
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="courage",
        current=12,
        starting=10,
        direction=MetricDirection.Descending,
        threshold_high=10,
        threshold_low=0,
    )
    enc.encounter_type = "combat"
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    # Descending metric now at 14; threshold_high=10 is crossed but must be
    # ignored. Only threshold_low=0 resolves on Descending.
    assert snap.encounter.resolved is False
    assert snap.encounter.metric.current == 14


def test_phase_ladder_beat_four_is_escalation(cac_snap) -> None:
    """Beat 4 is still Escalation; Climax starts at beat 5 (matches Rust)."""
    from sidequest.game.encounter import (
        EncounterMetric,
        EncounterPhase,
        MetricDirection,
        StructuredEncounter,
    )
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum",
        current=0,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=100,  # wide so we don't resolve on threshold
        threshold_low=-100,
    )
    enc.beat = 3
    snap.encounter = enc
    result = NarrationTurnResult(
        narration="",
        beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    assert snap.encounter.beat == 4
    assert snap.encounter.structured_phase == EncounterPhase.Escalation


# ---------------------------------------------------------------------------
# Lie-detector: confrontation-trigger with empty npcs_present
# (pingpong 2026-04-24 — "Confrontation panel has no enemy combatants")
# ---------------------------------------------------------------------------


def test_confrontation_trigger_with_empty_npcs_present_fires_empty_actor_list_span(
    cac_snap, otel_capture: InMemorySpanExporter
) -> None:
    """Narrator emits confrontation but no npcs_present → encounter is
    instantiated with only the player, and the lie-detector span fires so the
    GM panel can surface that the extraction dropped the adversary list.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    snap.genre_slug = "caverns_and_claudes"
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[],  # narrator named goblins in prose but omitted them here
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )

    # Encounter still instantiated (with player-only combatant list)
    assert snap.encounter is not None
    assert snap.encounter.encounter_type == "combat"

    # Lie-detector span fired
    spans_by_name = {s.name: s for s in otel_capture.get_finished_spans()}
    assert "encounter.empty_actor_list" in spans_by_name, (
        f"expected encounter.empty_actor_list span; got {list(spans_by_name)}"
    )
    s = spans_by_name["encounter.empty_actor_list"]
    assert s.attributes["encounter_type"] == "combat"
    assert s.attributes["player_name"] == "Rux"
    assert s.attributes["genre_slug"] == "caverns_and_claudes"


def test_confrontation_trigger_with_populated_npcs_present_does_not_fire_span(
    cac_snap, otel_capture: InMemorySpanExporter
) -> None:
    """Healthy case — when npcs_present carries adversaries, the
    lie-detector stays quiet. Asserts the span is scoped to the extraction
    failure, not every confrontation.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = cac_snap
    snap.genre_slug = "caverns_and_claudes"
    result = NarrationTurnResult(
        narration="Goblins leap from the shadows.",
        confrontation="combat",
        npcs_present=[NpcMention(name="Goblin pack", role="hostile", is_new=True)],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Rux", pack=pack,
    )
    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "encounter.empty_actor_list" not in span_names
