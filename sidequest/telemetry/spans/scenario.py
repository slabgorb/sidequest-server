"""Scenario spans — clue-graph advance and accusation."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_SCENARIO_ADVANCE = "scenario.advance"
SPAN_SCENARIO_ACCUSATION = "scenario.accusation"
SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION = "scenario.clue_prerequisite_violation"

FLAT_ONLY_SPANS.update(
    {
        SPAN_SCENARIO_ADVANCE,
        SPAN_SCENARIO_ACCUSATION,
        SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION,
    }
)
