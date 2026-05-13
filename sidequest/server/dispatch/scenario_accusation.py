"""Scenario accusation intake — ADR-053 AC-5 wiring (Story 50-8).

Bridges the narration-response path to the bound :class:`AccusationEvaluator`:

- Reads the active character's ``known_facts`` filtered to entries with
  ``source == "ScenarioClue"`` (the provenance tag minted by Story
  50-5's :func:`sidequest.server.dispatch.scenario_clue_intake.consume_clue_footnotes`).
- Builds one :class:`EvidenceItem` per filtered fact. The
  ``clue_id`` field is recovered by walking the scenario's
  :class:`ClueGraph` nodes in declaration order, in parallel with the
  ScenarioClue-sourced known facts. ``KnownFact`` does not carry the
  originating clue id back through Story 50-5's mint path — that
  type-promotion is tracked under 50-17 (ADR-100 J-4). Until the field
  lands, parallel-iteration is the cleanest deterministic recovery.
- Delegates to :meth:`AccusationEvaluator.evaluate` and returns the
  resulting :class:`EvidenceSummary` for the caller to render.

Sibling of :mod:`sidequest.server.dispatch.scenario_clue_intake` (clue
discovery → KnownFact) and :mod:`sidequest.server.dispatch.scenario_bind`
(chargen → ScenarioState binding). Together the three form the
scenario-system dispatch trio.
"""

from __future__ import annotations

from sidequest.game.accusation import (
    AccusationEvaluator,
    EvidenceItem,
    EvidenceSummary,
)
from sidequest.game.character import KnownFact
from sidequest.game.session import GameSnapshot

# Confidence values that surface in EvidenceItem. ``confirmed`` (the
# default KnownFact tier produced by non-scenario sources) is excluded
# along with anything outside the scenario subsystem's confidence set —
# the evaluator's Literal type would reject them anyway, but we filter
# explicitly so the caller sees a clear "no scenario evidence" outcome
# rather than a ValidationError.
_SUPPORTED_CONFIDENCES = {"Certain", "Suspected", "Rumored", "Discovered"}


def consume_accusation_request(
    snapshot: GameSnapshot,
    accused_npc: str,
    active_character_name: str,
) -> EvidenceSummary | None:
    """Build evidence from scenario-sourced known facts and evaluate.

    Returns ``None`` when no scenario is bound to the snapshot — the
    shim refuses to fabricate verdicts in worlds without a clue graph.
    """
    scenario = snapshot.scenario_state
    if scenario is None:
        return None

    active = next(
        (c for c in snapshot.characters if c.core.name == active_character_name),
        None,
    )
    if active is None:
        return EvidenceSummary(
            accused_npc=accused_npc,
            evidence=[],
            verdict="circumstantial",
            score=0.0,
            rationale=(
                f"No active character named {active_character_name!r} found; "
                "cannot assemble evidence."
            ),
        )

    evidence = _build_evidence(active.known_facts, scenario_state=scenario)
    if not evidence:
        return EvidenceSummary(
            accused_npc=accused_npc,
            evidence=[],
            verdict="circumstantial",
            score=0.0,
            rationale=(
                "No scenario-sourced evidence in the active character's known "
                "facts; verdict defaults to Circumstantial pending discovery."
            ),
        )

    evaluator = AccusationEvaluator()
    return evaluator.evaluate(
        scenario=scenario,
        accused_npc=accused_npc,
        evidence=evidence,
    )


def _build_evidence(
    known_facts: list[KnownFact],
    *,
    scenario_state,
) -> list[EvidenceItem]:
    """Convert ScenarioClue-sourced known facts into evidence items.

    Walks the scenario's clue-graph nodes in declaration order in
    parallel with the filtered known facts; if the fact count exceeds
    the node count, surplus facts reuse the last node id. The clue id
    is load-bearing for red-herring detection, but ``KnownFact`` does
    not currently carry an originating clue id (see module docstring) —
    parallel iteration is the deterministic fallback until 50-17 lands.
    """
    nodes = scenario_state.clue_graph.nodes
    if not nodes:
        return []

    items: list[EvidenceItem] = []
    node_idx = 0
    for fact in known_facts:
        if fact.source != "ScenarioClue":
            continue
        if fact.confidence not in _SUPPORTED_CONFIDENCES:
            continue
        clue_id = nodes[min(node_idx, len(nodes) - 1)].id
        node_idx += 1
        items.append(
            EvidenceItem(
                clue_id=clue_id,
                description=fact.content,
                confidence=fact.confidence,
                chain_of_custody=[],
                contribution="helps",
            )
        )
    return items


__all__ = ["consume_accusation_request"]
