from __future__ import annotations

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, BeatSelection
from sidequest.genre.loader import GenreLoader, DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.game.session import GameSnapshot


@pytest.fixture
def cac_snap():
    snap = GameSnapshot(genre="caverns_and_claudes")
    pack = GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load("caverns_and_claudes")
    return snap, pack


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
    from sidequest.game.encounter import StructuredEncounter, EncounterMetric, MetricDirection
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
