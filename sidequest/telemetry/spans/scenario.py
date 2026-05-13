"""Scenario spans — clue-graph advance, accusation, gossip propagation."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_SCENARIO_ADVANCE = "scenario.advance"
SPAN_SCENARIO_ACCUSATION = "scenario.accusation"
SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION = "scenario.clue_prerequisite_violation"
SPAN_GOSSIP_PROPAGATION = "scenario.gossip_propagation"
SPAN_BELIEF_STATE_MUTATION = "scenario.belief_state_mutation"

FLAT_ONLY_SPANS.update(
    {
        SPAN_SCENARIO_ADVANCE,
        SPAN_SCENARIO_ACCUSATION,
        SPAN_SCENARIO_CLUE_PREREQUISITE_VIOLATION,
        SPAN_GOSSIP_PROPAGATION,
        SPAN_BELIEF_STATE_MUTATION,
    }
)
