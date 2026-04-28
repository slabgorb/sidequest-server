"""Scenario spans — clue-graph advance and accusation."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_SCENARIO_ADVANCE = "scenario.advance"
SPAN_SCENARIO_ACCUSATION = "scenario.accusation"

FLAT_ONLY_SPANS.update({SPAN_SCENARIO_ADVANCE, SPAN_SCENARIO_ACCUSATION})
