"""Rule-based accusation verdict for the Scenario System.

Implements ADR-053's ``AccusationEvaluator`` for the SideQuest scenario
subsystem. The evaluator scores assembled :class:`EvidenceItem` records
against a bound :class:`~sidequest.game.scenario_state.ScenarioState`
and produces a deterministic :class:`EvidenceSummary` with a verdict in
the Circumstantial / Strong / Airtight band. The narrator dramatizes
the summary — it does NOT determine the verdict.

Scoring contract:

- Per confidence (raw item weight):
    * ``Certain``    = 2.0  — direct, witnessed knowledge
    * ``Suspected``  = 1.0  — uncertain, evidence-supported
    * ``Rumored``    = 0.5  — gossip, untrusted source
    * ``Discovered`` = 1.5  — server-minted from a ScenarioClue footnote
- Per contribution: ``helps`` = +1, ``hurts`` = -1, ``neutral`` = 0
- Per chain-of-custody hop: raw × 0.7^len(chain_of_custody) — indirect
  testimony decays at the same rate as gossip propagation (see
  ``GossipEngine.decay_per_hop`` default)
- Red-herring clues (``ClueNode.red_herring is True`` in the scenario's
  clue graph): contribute 0 regardless of confidence — the narrator
  may dramatize the player's confusion, but the rule layer refuses to
  award points for a red herring

Verdict bands (default thresholds):

    score < strong_threshold (3.0)        → Circumstantial
    strong_threshold ≤ score < airtight   → Strong
    score ≥ airtight_threshold (5.0)      → Airtight

Lower bounds are inclusive — a score of exactly ``strong_threshold``
lands Strong, not Circumstantial.

OTEL: every :meth:`AccusationEvaluator.evaluate` call emits
:data:`SPAN_SCENARIO_ACCUSATION` with the full audit trail
(``accused_npc``, ``verdict``, ``score``, ``evidence_count``,
``strong_threshold``, ``airtight_threshold``, ``matches_guilty``). The
GM panel reads this span directly — it is the lie detector for
accusation logic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sidequest.game.scenario_state import ScenarioState
from sidequest.telemetry.spans import SPAN_SCENARIO_ACCUSATION, Span

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class AccusationVerdict:
    """String constants for the three verdict bands.

    Plain-string constants (not Enum) mirror the existing scenario
    pattern in this codebase — see :class:`ScenarioRole` and
    :class:`ClaimSentiment`. The values land in OTEL attributes
    verbatim, so downstream watchers can match on the same tokens.
    """

    Circumstantial = "circumstantial"
    Strong = "strong"
    Airtight = "airtight"


_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "Certain": 2.0,
    "Suspected": 1.0,
    "Rumored": 0.5,
    "Discovered": 1.5,
}

_CONTRIBUTION_MULTIPLIERS: dict[str, float] = {
    "helps": 1.0,
    "hurts": -1.0,
    "neutral": 0.0,
}

_DECAY_PER_HOP: float = 0.7


# ---------------------------------------------------------------------------
# EvidenceItem — one piece of evidence in an accusation.
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """A single piece of evidence the player offers in an accusation.

    Fields are typed Literals where possible so the constructor rejects
    unknown confidence/contribution tokens at the boundary — no silent
    coercion to a default value (CLAUDE.md "No Silent Fallbacks").
    """

    model_config = {"extra": "forbid"}

    clue_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: Literal["Certain", "Suspected", "Rumored", "Discovered"]
    chain_of_custody: list[str] = Field(default_factory=list)
    contribution: Literal["helps", "hurts", "neutral"] = "helps"


# ---------------------------------------------------------------------------
# EvidenceSummary — the verdict + audit trail.
# ---------------------------------------------------------------------------


class EvidenceSummary(BaseModel):
    """Verdict computation result with full audit trail.

    The narrator reads this summary and dramatizes it; it must contain
    everything a curious player or the GM panel needs to ask "why?".
    """

    model_config = {"extra": "forbid"}

    accused_npc: str
    evidence: list[EvidenceItem]
    verdict: Literal["circumstantial", "strong", "airtight"]
    score: float
    rationale: str


# ---------------------------------------------------------------------------
# AccusationEvaluator
# ---------------------------------------------------------------------------


class AccusationEvaluator:
    """Rule-based verdict computation for player accusations.

    Construct once per session; call :meth:`evaluate` per accusation.
    Thresholds are configurable at construction so genre packs can tune
    how forgiving the rule layer is — a noir mystery may want a harsher
    Airtight bar than a cozy parlour-room whodunit.
    """

    def __init__(
        self,
        *,
        strong_threshold: float = 3.0,
        airtight_threshold: float = 5.0,
    ) -> None:
        self.strong_threshold = strong_threshold
        self.airtight_threshold = airtight_threshold

    def evaluate(
        self,
        *,
        scenario: ScenarioState,
        accused_npc: str,
        evidence: list[EvidenceItem],
    ) -> EvidenceSummary:
        """Score ``evidence`` against ``scenario`` and return a verdict.

        Raises:
            ValueError: if ``accused_npc`` is empty or ``evidence`` is
                empty. The rule layer refuses to fabricate a verdict
                from no input — CLAUDE.md "No Silent Fallbacks".
        """
        if not accused_npc:
            raise ValueError(
                "AccusationEvaluator.evaluate: accused_npc cannot be empty — "
                "an accusation must name a target."
            )
        if not evidence:
            raise ValueError(
                "AccusationEvaluator.evaluate: evidence list cannot be empty — "
                "the rule layer refuses to fabricate a verdict from no input."
            )

        red_herring_ids = {node.id for node in scenario.clue_graph.nodes if node.red_herring}

        score = 0.0
        for item in evidence:
            if item.clue_id in red_herring_ids:
                continue
            raw = _CONFIDENCE_WEIGHTS[item.confidence]
            signed = raw * _CONTRIBUTION_MULTIPLIERS[item.contribution]
            score += signed * (_DECAY_PER_HOP ** len(item.chain_of_custody))

        verdict = self._verdict_for(score)
        rationale = self._rationale(score=score, verdict=verdict, evidence_count=len(evidence))
        matches_guilty = bool(scenario.guilty_npc) and (scenario.guilty_npc == accused_npc)

        with Span.open(
            SPAN_SCENARIO_ACCUSATION,
            {
                "accused_npc": accused_npc,
                "verdict": verdict,
                "score": score,
                "evidence_count": len(evidence),
                "strong_threshold": self.strong_threshold,
                "airtight_threshold": self.airtight_threshold,
                "matches_guilty": matches_guilty,
            },
        ):
            return EvidenceSummary(
                accused_npc=accused_npc,
                evidence=list(evidence),
                verdict=verdict,
                score=score,
                rationale=rationale,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _verdict_for(self, score: float) -> str:
        """Map ``score`` to a verdict band with inclusive lower bounds."""
        if score >= self.airtight_threshold:
            return AccusationVerdict.Airtight
        if score >= self.strong_threshold:
            return AccusationVerdict.Strong
        return AccusationVerdict.Circumstantial

    def _rationale(self, *, score: float, verdict: str, evidence_count: int) -> str:
        """Compose a non-blank rationale string for the audit trail."""
        return (
            f"{verdict.capitalize()} verdict from {evidence_count} evidence item(s): "
            f"score {score:.2f} against thresholds "
            f"strong={self.strong_threshold:.2f}, airtight={self.airtight_threshold:.2f}."
        )


__all__ = [
    "AccusationEvaluator",
    "AccusationVerdict",
    "EvidenceItem",
    "EvidenceSummary",
]
