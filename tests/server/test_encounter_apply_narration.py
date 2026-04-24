from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult, NpcMention
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack

# Load directly from the fixture pack on disk, bypassing GenreLoader's
# session-wide cache. The session cache keys on slug only, so another test
# that loads ``caverns_and_claudes`` from ``sidequest-content/`` (e.g.
# test_opening_turn_bootstrap.py) poisons the cache with the real content
# pack — which is missing the ``mutant_ability`` / ``flank`` beats these
# tests need. load_genre_pack() is the cache-free path.
_FIXTURE_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "test_genre"
)


def _load_pack(_genre: str):
    # ``_genre`` is intentionally ignored — every fixture slug symlinks to
    # the same ``test_genre`` directory, and we skip the cache to avoid
    # cross-test pack poisoning. Callers still pass a slug for readability.
    return load_genre_pack(_FIXTURE_PACK)


@pytest.fixture
def cac_snap():
    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = _load_pack("caverns_and_claudes")
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


# ---------------------------------------------------------------------------
# ADR-074 dice integration: failed roll applies beat's failure_metric_delta
# (pingpong 2026-04-24 — "Momentum increments on a failed Use Mutation roll")
# ---------------------------------------------------------------------------


@pytest.fixture
def mw_snap_with_combat():
    """Fresh snapshot with a live Wasteland Brawl.

    Loads via the ``caverns_and_claudes`` slug because the test fixture
    directory symlinks every genre to the frozen ``test_genre`` pack (a
    mutant_wasteland copy). The resolved pack has ``type: combat`` with
    ``mutant_ability`` + ``flank`` beats carrying the new structured
    failure branch — no separate ``mutant_wasteland`` symlink exists in
    ``tests/fixtures/packs/``.
    """
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )

    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = _load_pack("caverns_and_claudes")
    enc = StructuredEncounter.combat(combatants=["Slabgorb"], hp=10)
    enc.encounter_type = "combat"
    enc.metric = EncounterMetric(
        name="momentum", current=0, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    snap.encounter = enc
    return snap, pack


def test_failed_use_mutation_applies_failure_metric_delta(
    mw_snap_with_combat, otel_capture: InMemorySpanExporter
) -> None:
    """Rolling Fail on Use Mutation (beat has failure_metric_delta=-2)
    should subtract 2 from momentum, NOT add the default +4.

    Regression for pingpong 2026-04-24 "Momentum increments on a failed
    Use Mutation roll". The engine previously applied ``metric_delta``
    regardless of dice outcome; the YAML ``risk`` field was documentation
    only and never reached the reducer. Now the structured failure branch
    runs when dice_failed=True.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="The mutation turns on you.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="mutant_ability", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack, dice_failed=True,
    )
    # Momentum moves by failure_metric_delta (-2), not the default +4.
    assert snap.encounter.metric.current == -2, (
        f"expected momentum -2 on failed Use Mutation; got "
        f"{snap.encounter.metric.current}"
    )

    # Lie-detector span fired so GM panel can see the failure branch paid out.
    failure_spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "encounter.beat_failure_branch"
    ]
    assert len(failure_spans) == 1
    s = failure_spans[0]
    assert s.attributes["beat_id"] == "mutant_ability"
    assert s.attributes["actor"] == "Slabgorb"
    assert s.attributes["base_delta"] == 4
    assert s.attributes["failure_delta"] == -2


def test_succeeded_use_mutation_applies_default_metric_delta(
    mw_snap_with_combat, otel_capture: InMemorySpanExporter
) -> None:
    """Rolling Success on Use Mutation applies the default +4 and does
    NOT fire the failure-branch span. Verifies the success path is
    untouched by the new branching.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="The mutation flares.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="mutant_ability", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack, dice_failed=False,
    )
    assert snap.encounter.metric.current == 4

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "encounter.beat_failure_branch" not in span_names


def test_beat_without_failure_branch_keeps_default_delta_on_fail(
    mw_snap_with_combat, otel_capture: InMemorySpanExporter
) -> None:
    """When a beat has NO ``failure_metric_delta`` set (e.g. ``attack``),
    dice_failed=True keeps the default delta — legacy behavior preserved.
    Regression guard against accidentally breaking beats that don't define
    a structured failure branch.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="You swing.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="attack", target=None),
        ],
    )
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack, dice_failed=True,
    )
    # attack has metric_delta=2, no failure branch — still applies +2.
    assert snap.encounter.metric.current == 2

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert "encounter.beat_failure_branch" not in span_names


def test_dice_failed_none_preserves_legacy_behavior(
    mw_snap_with_combat
) -> None:
    """dice_failed=None (no roll attached to this turn) applies default
    metric_delta unconditionally — matches pre-dice-integration behavior.
    """
    from sidequest.server.session_handler import _apply_narration_result_to_snapshot

    snap, pack = mw_snap_with_combat
    result = NarrationTurnResult(
        narration="You unleash the mutation.",
        beat_selections=[
            BeatSelection(actor="Slabgorb", beat_id="mutant_ability", target=None),
        ],
    )
    # Default call — no dice_failed kwarg. Same as every existing call site
    # before the ADR-074 wiring lands fully.
    _apply_narration_result_to_snapshot(
        snap, result, player_name="Slabgorb", pack=pack,
    )
    assert snap.encounter.metric.current == 4
