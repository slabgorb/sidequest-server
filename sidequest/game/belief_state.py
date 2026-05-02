"""Per-NPC belief bubbles for the Scenario System.

Each NPC carries a :class:`BeliefState` — a list of :class:`Belief`
records (``Fact`` / ``Suspicion`` / ``Claim``) and a map of trust
scores (:class:`Credibility`) for other named NPCs. Story 2.3 Slice D
covers only the data model and mutation surface needed by scenario
binding at chargen confirmation: :meth:`BeliefState.add_belief`,
:meth:`BeliefState.beliefs_about`, :meth:`BeliefState.credibility_of`,
:meth:`BeliefState.update_credibility`. Gossip propagation and
accusation evaluation land in a later slice.

OTEL: :meth:`add_belief` and :meth:`update_credibility` emit watcher
events on the current span (``belief_state.belief_added`` /
``belief_state.credibility_updated``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from opentelemetry import trace
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# BeliefSource — how the NPC came to hold a belief.
# ---------------------------------------------------------------------------


class BeliefSourceWitnessed(BaseModel):
    """NPC saw or sensed the referenced event directly."""

    model_config = {"extra": "forbid"}

    kind: Literal["witnessed"] = "witnessed"


class BeliefSourceToldBy(BaseModel):
    """NPC was told by another specific NPC (name carried)."""

    model_config = {"extra": "forbid"}

    kind: Literal["told_by"] = "told_by"
    by: str


class BeliefSourceInferred(BaseModel):
    """NPC deduced the belief from available context."""

    model_config = {"extra": "forbid"}

    kind: Literal["inferred"] = "inferred"


class BeliefSourceOverheard(BaseModel):
    """NPC heard the belief indirectly (gossip / eavesdropping)."""

    model_config = {"extra": "forbid"}

    kind: Literal["overheard"] = "overheard"


BeliefSource = Annotated[
    BeliefSourceWitnessed | BeliefSourceToldBy | BeliefSourceInferred | BeliefSourceOverheard,
    Field(discriminator="kind"),
]


def _source_label(source: BeliefSource) -> str:
    """OTEL label for a belief source."""
    if isinstance(source, BeliefSourceToldBy):
        return f"told_by:{source.by}"
    return source.kind


# ---------------------------------------------------------------------------
# ClaimSentiment — typed at creation, not keyword-parsed.
# ---------------------------------------------------------------------------


class ClaimSentiment:
    """Sentiment of a Claim relative to guilt — typed enum values."""

    Corroborating = "corroborating"
    Contradicting = "contradicting"
    Neutral = "neutral"


# ---------------------------------------------------------------------------
# Belief — tagged union over Fact / Suspicion / Claim.
# ---------------------------------------------------------------------------


class BeliefFact(BaseModel):
    """Confirmed knowledge — the NPC holds this as certain."""

    model_config = {"extra": "forbid"}

    variant: Literal["fact"] = "fact"
    subject: str
    content: str
    turn_learned: int = 0
    source: BeliefSource


class BeliefSuspicion(BaseModel):
    """Uncertain belief with a confidence score in ``[0.0, 1.0]``."""

    model_config = {"extra": "forbid"}

    variant: Literal["suspicion"] = "suspicion"
    subject: str
    content: str
    turn_learned: int = 0
    source: BeliefSource
    confidence: float

    @classmethod
    def make(
        cls,
        subject: str,
        content: str,
        turn_learned: int,
        source: BeliefSource,
        confidence: float,
    ) -> BeliefSuspicion:
        """Clamped constructor — confidence is forced into ``[0.0, 1.0]``."""
        return cls(
            subject=subject,
            content=content,
            turn_learned=turn_learned,
            source=source,
            confidence=max(0.0, min(1.0, confidence)),
        )


class BeliefClaim(BaseModel):
    """A statement made by another NPC, which may or may not be believed."""

    model_config = {"extra": "forbid"}

    variant: Literal["claim"] = "claim"
    subject: str
    content: str
    turn_learned: int = 0
    source: BeliefSource
    believed: bool
    sentiment: Literal["corroborating", "contradicting", "neutral"] = "neutral"


Belief = Annotated[
    BeliefFact | BeliefSuspicion | BeliefClaim,
    Field(discriminator="variant"),
]


# ---------------------------------------------------------------------------
# Credibility — clamped trust score (0.0..=1.0), default 0.5.
# ---------------------------------------------------------------------------


class Credibility(BaseModel):
    """Trust score for another NPC — clamped to ``[0.0, 1.0]``."""

    model_config = {"extra": "forbid"}

    score: float = 0.5

    @classmethod
    def new(cls, score: float) -> Credibility:
        return cls(score=max(0.0, min(1.0, score)))

    def adjust(self, delta: float) -> None:
        self.score = max(0.0, min(1.0, self.score + delta))


# ---------------------------------------------------------------------------
# BeliefState — the per-NPC container.
# ---------------------------------------------------------------------------


class BeliefState(BaseModel):
    """Per-NPC knowledge container.

    Holds a list of beliefs and a map of credibility scores keyed by
    NPC name. Mutation methods emit OTEL watcher events so the GM
    panel can verify scenario seeding and gossip propagation are
    actually firing.
    """

    model_config = {"extra": "forbid"}

    beliefs: list[BeliefFact | BeliefSuspicion | BeliefClaim] = Field(default_factory=list)
    credibility_scores: dict[str, Credibility] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Belief mutation
    # ------------------------------------------------------------------

    def add_belief(self, belief: BeliefFact | BeliefSuspicion | BeliefClaim) -> None:
        """Append a belief and emit a ``belief_state.belief_added`` event."""
        self.beliefs.append(belief)
        span = trace.get_current_span()
        span.add_event(
            "belief_state.belief_added",
            {
                "action": "belief_added",
                "variant": belief.variant,
                "subject": belief.subject,
                "content": belief.content,
                "source": _source_label(belief.source),
                "turn_learned": belief.turn_learned,
                "beliefs_count_after": len(self.beliefs),
            },
        )

    def beliefs_about(self, subject: str) -> list[BeliefFact | BeliefSuspicion | BeliefClaim]:
        """Return every belief whose ``subject`` equals ``subject`` (exact match)."""
        return [b for b in self.beliefs if b.subject == subject]

    # ------------------------------------------------------------------
    # Credibility graph
    # ------------------------------------------------------------------

    def credibility_of(self, npc_name: str) -> Credibility:
        """Return the credibility for ``npc_name``, defaulting to 0.5."""
        existing = self.credibility_scores.get(npc_name)
        if existing is None:
            return Credibility()
        # Return a copy so callers can't mutate the stored record by accident.
        return Credibility(score=existing.score)

    def update_credibility(self, npc_name: str, score: float) -> None:
        """Set ``npc_name``'s credibility (clamped) and emit a watcher event."""
        previous = (
            self.credibility_scores[npc_name].score if npc_name in self.credibility_scores else None
        )
        clamped = Credibility.new(score)
        self.credibility_scores[npc_name] = clamped

        span = trace.get_current_span()
        span.add_event(
            "belief_state.credibility_updated",
            {
                "action": "credibility_updated",
                "target_npc": npc_name,
                "previous_score": previous if previous is not None else -1.0,
                "requested_score": score,
                "new_score": clamped.score,
            },
        )


__all__ = [
    "Belief",
    "BeliefClaim",
    "BeliefFact",
    "BeliefSource",
    "BeliefSourceInferred",
    "BeliefSourceOverheard",
    "BeliefSourceToldBy",
    "BeliefSourceWitnessed",
    "BeliefState",
    "BeliefSuspicion",
    "ClaimSentiment",
    "Credibility",
]
