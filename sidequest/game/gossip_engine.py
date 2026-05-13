"""Two-phase gossip propagation across NPC belief states.

Implements ADR-053's `GossipEngine` for the SideQuest scenario subsystem.
Per-turn, the engine receives a batch of :class:`GossipTransmission`
records (each: ``from_npc`` tells ``to_npc`` about ``subject``) and
applies them under a snapshot-then-mutate discipline so the order of
transmissions within a single tick cannot influence the outcome.

Storage convention: gossip-arrived beliefs land in the receiver's
``BeliefState`` as :class:`BeliefSuspicion` records — "rumor tier" —
with ``confidence`` equal to the gossip's post-decay credibility. Gossip
never promotes to :class:`BeliefFact`; certainty is reserved for direct
witnessing. Existing facts in the receiver's belief state are never
overwritten — contradicting gossip is flagged on the outcome and stored
alongside the fact as low-confidence rumor (the "downgrade to rumor
tier" path described in ADR-053).

Multi-hop lineage: when a sender forwards gossip about a subject it
already holds a belief about, the engine reads the sender's stored
confidence in that subject and caps ``credibility_before`` at the
minimum of (receiver's trust in sender, sender's own confidence in the
gossip). This is how A→B→C decays credibility strictly more than a
direct A→C transmission would.

OTEL: every transmission emits :data:`SPAN_GOSSIP_PROPAGATION`. Every
receiver mutation additionally emits :data:`SPAN_BELIEF_STATE_MUTATION`
nested inside the propagation span. Both are flat-only spans in the
catalog; the GM panel reads them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from sidequest.game.belief_state import (
    BeliefClaim,
    BeliefFact,
    BeliefSourceToldBy,
    BeliefState,
    BeliefSuspicion,
)
from sidequest.telemetry.spans import (
    SPAN_BELIEF_STATE_MUTATION,
    SPAN_GOSSIP_PROPAGATION,
    Span,
)

# ---------------------------------------------------------------------------
# Transmission + Outcome data classes
# ---------------------------------------------------------------------------


class GossipTransmission(BaseModel):
    """One NPC tells another NPC a single piece of gossip.

    Constructor-time validation enforces non-empty fields and no
    self-loops (`from_npc == to_npc`). Both are structural errors the
    engine refuses to silence — CLAUDE.md "No Silent Fallbacks".
    """

    model_config = {"extra": "forbid"}

    from_npc: str = Field(min_length=1)
    to_npc: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    content: str = Field(min_length=1)
    sentiment: Literal["corroborating", "contradicting", "neutral"] = "neutral"

    @model_validator(mode="after")
    def _reject_self_loop(self) -> GossipTransmission:
        if self.from_npc == self.to_npc:
            raise ValueError(
                f"GossipTransmission self-loop: {self.from_npc!r} cannot gossip to itself"
            )
        return self


class TransmissionOutcome(BaseModel):
    """Per-transmission result after engine processing.

    Surfaced in :class:`GossipResult` so callers can audit every
    transmission's fate without having to diff belief states.
    """

    model_config = {"extra": "forbid"}

    from_npc: str
    to_npc: str
    subject: str
    content: str
    credibility_before: float
    credibility_after: float
    accepted: bool
    contradicted: bool = False


class GossipResult(BaseModel):
    """Return value of :meth:`GossipEngine.propagate`."""

    model_config = {"extra": "forbid"}

    outcomes: list[TransmissionOutcome] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GossipEngine
# ---------------------------------------------------------------------------


class GossipEngine:
    """Two-phase belief-propagation engine for scenario NPCs.

    Construct once; call :meth:`propagate` per scenario tick with the
    batch of transmissions that should fire this turn.
    """

    def __init__(self, *, decay_per_hop: float = 0.1) -> None:
        self.decay_per_hop = decay_per_hop

    def propagate(
        self,
        *,
        npcs: dict[str, BeliefState],
        transmissions: list[GossipTransmission],
        current_turn: int = 0,
    ) -> GossipResult:
        """Apply a batch of transmissions to ``npcs`` under two-phase mutation.

        Phase 1 — snapshot: for each transmission, look up the receiver's
        credibility in the sender (snapshot value, not post-batch) and
        the sender's own confidence in the subject. Compute pre- and
        post-decay credibility plus contradiction flag. No mutations
        happen during Phase 1, so transmission order inside ``transmissions``
        cannot influence other transmissions in the same call.

        Phase 2 — integrate: emit a :data:`SPAN_GOSSIP_PROPAGATION` span
        per transmission. If the gossip's post-decay credibility is
        positive, append a :class:`BeliefSuspicion` to the receiver's
        belief state (rumor tier; never promoted to ``BeliefFact``) and
        emit a nested :data:`SPAN_BELIEF_STATE_MUTATION`. Existing facts
        are preserved; contradiction is surfaced on the outcome and the
        span but never silently dropped.

        Raises:
            KeyError: if any transmission targets a ``to_npc`` not in
                ``npcs`` — no silent fallback for unknown receivers.
        """
        snapshots: list[_Snapshot] = []
        for t in transmissions:
            if t.to_npc not in npcs:
                raise KeyError(
                    f"GossipTransmission targets unknown to_npc {t.to_npc!r} "
                    f"(not in npcs map: {sorted(npcs)})"
                )
            receiver = npcs[t.to_npc]
            sender = npcs.get(t.from_npc)

            trust = receiver.credibility_of(t.from_npc).score
            cred_before = trust
            if sender is not None:
                source_confidence = _sender_confidence(sender, t.subject)
                if source_confidence is not None:
                    cred_before = min(trust, source_confidence)

            cred_after = max(0.0, cred_before - self.decay_per_hop)
            contradicted = _has_contradicting_fact(receiver, t)
            accepted = cred_after > 0.0 and not contradicted

            snapshots.append(
                _Snapshot(
                    transmission=t,
                    credibility_before=cred_before,
                    credibility_after=cred_after,
                    accepted=accepted,
                    contradicted=contradicted,
                )
            )

        outcomes: list[TransmissionOutcome] = []
        for snap in snapshots:
            t = snap.transmission
            with Span.open(
                SPAN_GOSSIP_PROPAGATION,
                {
                    "from_npc": t.from_npc,
                    "to_npc": t.to_npc,
                    "subject": t.subject,
                    "sentiment": t.sentiment,
                    "credibility_before": snap.credibility_before,
                    "credibility_after": snap.credibility_after,
                    "accepted": snap.accepted,
                    "contradicted": snap.contradicted,
                    "current_turn": current_turn,
                },
            ):
                if snap.credibility_after > 0.0:
                    receiver = npcs[t.to_npc]
                    with Span.open(
                        SPAN_BELIEF_STATE_MUTATION,
                        {
                            "npc": t.to_npc,
                            "target_npc": t.to_npc,
                            "subject": t.subject,
                            "variant": "suspicion",
                            "confidence": snap.credibility_after,
                            "contradicted": snap.contradicted,
                            "current_turn": current_turn,
                        },
                    ):
                        receiver.add_belief(
                            BeliefSuspicion.make(
                                subject=t.subject,
                                content=t.content,
                                turn_learned=current_turn,
                                source=BeliefSourceToldBy(by=t.from_npc),
                                confidence=snap.credibility_after,
                            )
                        )

            outcomes.append(
                TransmissionOutcome(
                    from_npc=t.from_npc,
                    to_npc=t.to_npc,
                    subject=t.subject,
                    content=t.content,
                    credibility_before=snap.credibility_before,
                    credibility_after=snap.credibility_after,
                    accepted=snap.accepted,
                    contradicted=snap.contradicted,
                )
            )

        return GossipResult(outcomes=outcomes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """Phase-1 record for a single transmission. Internal.

    Frozen dataclass over pydantic — values are computed deterministically
    from validated inputs (no further validation needed), and the
    snapshot is consumed immediately within :meth:`GossipEngine.propagate`.
    """

    transmission: GossipTransmission
    credibility_before: float
    credibility_after: float
    accepted: bool
    contradicted: bool


def _sender_confidence(sender: BeliefState, subject: str) -> float | None:
    """Strongest confidence the sender holds about ``subject``.

    - :class:`BeliefFact`: 1.0 (witnessed/known with certainty)
    - :class:`BeliefSuspicion`: its ``confidence`` field
    - :class:`BeliefClaim`: 1.0 if ``believed`` else 0.0

    Returns ``None`` when the sender has no beliefs about the subject,
    so the engine falls back to receiver-trust alone for the credibility
    floor. Used for multi-hop lineage: a re-transmitter's stored
    confidence caps the gossip's outbound credibility.
    """
    beliefs = sender.beliefs_about(subject)
    if not beliefs:
        return None

    confidences: list[float] = []
    for b in beliefs:
        if isinstance(b, BeliefFact):
            confidences.append(1.0)
        elif isinstance(b, BeliefSuspicion):
            confidences.append(b.confidence)
        elif isinstance(b, BeliefClaim):
            confidences.append(1.0 if b.believed else 0.0)
    return max(confidences)


def _has_contradicting_fact(receiver: BeliefState, t: GossipTransmission) -> bool:
    """True when the receiver holds a BeliefFact about ``t.subject`` whose
    content differs from the incoming gossip.

    Distinct facts on the same subject indicate a contradiction the
    receiver should not silently absorb. Only Facts are considered;
    suspicions and claims are themselves uncertain and don't anchor a
    contradiction.
    """
    for b in receiver.beliefs_about(t.subject):
        if isinstance(b, BeliefFact) and b.content != t.content:
            return True
    return False


__all__ = [
    "GossipEngine",
    "GossipResult",
    "GossipTransmission",
    "TransmissionOutcome",
]
