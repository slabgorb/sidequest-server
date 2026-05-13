"""Tests for ``sidequest.game.gossip_engine`` — Story 50-7.

Covers the GossipEngine's two-phase belief propagation, contradiction
detection, credibility decay, and OTEL observability per ADR-053 (Scenario
System), plus rule-enforcement coverage from CLAUDE.md (No Silent Fallbacks,
Verify Wiring) and python lang-review rules #1, #6, #11.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import ValidationError

from sidequest.game.belief_state import (
    BeliefFact,
    BeliefSourceWitnessed,
    BeliefState,
    BeliefSuspicion,
)
from sidequest.game.gossip_engine import (
    GossipEngine,
    GossipResult,
    GossipTransmission,
    TransmissionOutcome,
)

# ---------------------------------------------------------------------------
# OTEL harness — uses the shared ``otel_capture`` fixture from
# ``tests/game/conftest.py`` (carries the Story 45-36 processor-clearing fix
# that prevents span bleed-through between tests).
# ---------------------------------------------------------------------------


def _spans_named(exporter: InMemorySpanExporter, name: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == name]


# ---------------------------------------------------------------------------
# AC1 — Two-phase belief propagation
#
# Phase 1 (transmission): credibility/topic filters applied against snapshot.
# Phase 2 (integration): receivers' belief states mutated; contradictions
# trigger dispute resolution. The snapshot eliminates intra-batch order
# dependence.
# ---------------------------------------------------------------------------


class TestTwoPhasePropagation:
    def test_propagate_returns_gossip_result_with_outcome_per_transmission(self) -> None:
        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.8)

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="Was in the library at 9pm",
                )
            ],
            current_turn=3,
        )

        assert isinstance(result, GossipResult), (
            "propagate must return GossipResult so the caller can audit outcomes"
        )
        assert len(result.outcomes) == 1, (
            "One transmission in, exactly one outcome out — no silent drops"
        )
        outcome = result.outcomes[0]
        assert isinstance(outcome, TransmissionOutcome)
        assert outcome.from_npc == "Alice"
        assert outcome.to_npc == "Bert"
        assert outcome.subject == "Erskine"
        assert outcome.content == "Was in the library at 9pm"

    def test_two_npc_chain_mutates_receiver_only(self) -> None:
        """AC1 — two-NPC chain: A→B updates B's belief state; A unchanged."""
        engine = GossipEngine()
        alice = BeliefState()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)

        engine.propagate(
            npcs={"Alice": alice, "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="killed the victim",
                    sentiment="corroborating",
                )
            ],
            current_turn=5,
        )

        # Receiver gained a belief about the subject.
        bert_beliefs = bert.beliefs_about("Erskine")
        assert len(bert_beliefs) == 1, (
            "B should have learned exactly one new belief from the transmission"
        )
        # Sender is untouched — gossip flows A→B, never reflected back.
        assert alice.beliefs == [], "Sender's belief_state must not be mutated"

    def test_snapshot_isolates_credibility_within_batch(self) -> None:
        """Two transmissions to the same receiver in one call see the same
        pre-batch credibility snapshot — no intra-batch contamination.

        Without snapshotting, if Alice's transmission updated Bert's view of
        Alice mid-batch, Carol's transmission could read a different number
        for Carol's own credibility just from list ordering. The whole point
        of two-phase mutation is that this can't happen.
        """
        engine = GossipEngine(decay_per_hop=0.0)
        alice = BeliefState()
        carol = BeliefState()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)
        bert.update_credibility("Carol", 0.4)

        result = engine.propagate(
            npcs={"Alice": alice, "Bert": bert, "Carol": carol},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="left at 9pm",
                ),
                GossipTransmission(
                    from_npc="Carol",
                    to_npc="Bert",
                    subject="Erskine",
                    content="left at 10pm",
                ),
            ],
            current_turn=1,
        )

        carol_outcome = next(o for o in result.outcomes if o.from_npc == "Carol")
        alice_outcome = next(o for o in result.outcomes if o.from_npc == "Alice")
        # Snapshot reads: Alice=0.9, Carol=0.4. Neither mutation may shift the
        # other's credibility_before reading.
        assert alice_outcome.credibility_before == pytest.approx(0.9)
        assert carol_outcome.credibility_before == pytest.approx(0.4)

    def test_empty_transmissions_returns_empty_outcomes(self) -> None:
        """No-op tick is allowed and emits no outcomes."""
        engine = GossipEngine()
        result = engine.propagate(
            npcs={"Alice": BeliefState()},
            transmissions=[],
            current_turn=0,
        )
        assert result.outcomes == []


# ---------------------------------------------------------------------------
# AC2 — Contradiction detection
#
# A new claim against an existing Fact must be flagged. The existing Fact is
# not silently overwritten. The receiver may store the contradicting gossip
# at "rumor tier" (Suspicion / unbelieved Claim) so the narrator can dramatize
# the conflict — but the canonical Fact stays canonical.
# ---------------------------------------------------------------------------


class TestContradictionDetection:
    def test_contradicting_gossip_against_existing_fact_is_flagged(self) -> None:
        """A→B says X, but B already KNOWS not-X. Flag contradiction; preserve fact."""
        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.7)
        bert.add_belief(
            BeliefFact(
                subject="Erskine",
                content="Was at the pub at 9pm",
                turn_learned=2,
                source=BeliefSourceWitnessed(),
            )
        )

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="Was in the library at 9pm",
                    sentiment="contradicting",
                )
            ],
            current_turn=4,
        )

        outcome = result.outcomes[0]
        assert outcome.contradicted is True, (
            "Contradicting gossip against existing Fact must set contradicted=True"
        )

        # The original BeliefFact must still be present and intact.
        facts = [b for b in bert.beliefs_about("Erskine") if isinstance(b, BeliefFact)]
        assert len(facts) == 1, "Existing BeliefFact must not be removed by gossip"
        assert facts[0].content == "Was at the pub at 9pm"

        # When post-decay credibility remains positive (trust=0.7, default
        # decay=0.1 → 0.6), the contradicting gossip lands alongside the Fact
        # as a low-confidence Suspicion — the "rumor tier" path the engine
        # docstring describes. The Fact stays canonical, but the dispute is
        # visible to the narrator.
        suspicions = [b for b in bert.beliefs_about("Erskine") if isinstance(b, BeliefSuspicion)]
        assert len(suspicions) == 1, (
            "Contradicting gossip with positive credibility must be appended as "
            "a Suspicion alongside the Fact (rumor-tier dispute visibility)."
        )
        assert suspicions[0].content == "Was in the library at 9pm"
        assert suspicions[0].confidence == pytest.approx(0.6)

    def test_contradicting_gossip_with_zero_credibility_is_not_stored(self) -> None:
        """Pin the docstring promise: contradicting gossip is stored alongside
        the Fact ONLY when post-decay credibility remains positive. A
        zero-credibility contradiction is flagged on the outcome but NOT
        stored — the receiver's belief state stays clean. Without this test,
        the engine's documented contract about conditional storage can drift
        silently.
        """
        engine = GossipEngine(decay_per_hop=0.5)
        bert = BeliefState()
        bert.update_credibility("Alice", 0.3)  # post-decay: max(0, 0.3-0.5) = 0.0
        bert.add_belief(
            BeliefFact(
                subject="Erskine",
                content="Was at the pub",
                turn_learned=1,
                source=BeliefSourceWitnessed(),
            )
        )

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="Was in the library",
                    sentiment="contradicting",
                )
            ],
            current_turn=2,
        )

        # Contradiction surfaces on the outcome…
        assert result.outcomes[0].contradicted is True
        assert result.outcomes[0].accepted is False
        assert result.outcomes[0].credibility_after == pytest.approx(0.0)

        # …but the Fact is intact and no Suspicion was added.
        facts = [b for b in bert.beliefs_about("Erskine") if isinstance(b, BeliefFact)]
        suspicions = [b for b in bert.beliefs_about("Erskine") if isinstance(b, BeliefSuspicion)]
        assert len(facts) == 1
        assert facts[0].content == "Was at the pub"
        assert suspicions == [], (
            "Zero-credibility contradicting gossip must not be stored as a "
            "Suspicion — the receiver's trust in the source post-decay was nil."
        )

    def test_low_credibility_source_never_promotes_to_fact(self) -> None:
        """AC4 (credibility downgrade): low-trust source lands as rumor, not fact."""
        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.2)  # very low trust

        engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="is guilty",
                )
            ],
            current_turn=1,
        )

        facts = [b for b in bert.beliefs_about("Erskine") if isinstance(b, BeliefFact)]
        assert facts == [], "Gossip from a low-credibility source must never be stored as Fact"

    def test_high_credibility_source_records_outcome_as_accepted(self) -> None:
        """High trust + no contradiction → accepted=True on the outcome."""
        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="left a bloody glove",
                )
            ],
            current_turn=2,
        )
        assert result.outcomes[0].accepted is True
        assert result.outcomes[0].contradicted is False


# ---------------------------------------------------------------------------
# AC3 — Credibility decay
#
# Gossip credibility drops by `decay_per_hop` per transmission. Clamped at
# zero. Multi-hop A→B→C decays more than a single A→C hop would.
# ---------------------------------------------------------------------------


class TestCredibilityDecay:
    def test_decay_per_hop_reduces_credibility(self) -> None:
        engine = GossipEngine(decay_per_hop=0.2)
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(from_npc="Alice", to_npc="Bert", subject="x", content="y")
            ],
            current_turn=0,
        )
        outcome = result.outcomes[0]
        assert outcome.credibility_before == pytest.approx(0.9)
        assert outcome.credibility_after == pytest.approx(0.7)

    def test_decay_clamps_at_zero(self) -> None:
        """A heavy decay against a low-trust source clamps to 0.0, never negative."""
        engine = GossipEngine(decay_per_hop=0.5)
        bert = BeliefState()
        bert.update_credibility("Alice", 0.3)

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(from_npc="Alice", to_npc="Bert", subject="x", content="y")
            ],
            current_turn=0,
        )
        assert result.outcomes[0].credibility_after == pytest.approx(0.0)

    def test_multi_hop_decays_more_than_single_hop(self) -> None:
        """AC4 (multi-hop): A→B→C across two turns yields lower credibility
        at C than a direct single-hop A→C transmission would.

        Bert hears Alice's gossip with trust 0.9 → carry credibility 0.7
        (one decay of 0.2). When Bert later relays to Carol — who also
        trusts Bert at 0.9 — the gossip must arrive with credibility
        strictly below 0.7 because its lineage now carries two hops of
        decay, not one.
        """
        engine = GossipEngine(decay_per_hop=0.2)
        alice = BeliefState()
        bert = BeliefState()
        carol = BeliefState()
        bert.update_credibility("Alice", 0.9)
        carol.update_credibility("Bert", 0.9)

        # Turn 1: A → B
        r1 = engine.propagate(
            npcs={"Alice": alice, "Bert": bert, "Carol": carol},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="is guilty",
                )
            ],
            current_turn=1,
        )
        assert r1.outcomes[0].credibility_after == pytest.approx(0.7)

        # The Suspicion Bert now holds about Erskine carries confidence 0.7;
        # this is the value Bert's onward gossip in turn 2 must read back.
        bert_beliefs = bert.beliefs_about("Erskine")
        suspicions = [b for b in bert_beliefs if isinstance(b, BeliefSuspicion)]
        assert len(suspicions) == 1
        assert suspicions[0].confidence == pytest.approx(0.7)

        # Turn 2: B → C, relaying what B heard from A.
        r2 = engine.propagate(
            npcs={"Alice": alice, "Bert": bert, "Carol": carol},
            transmissions=[
                GossipTransmission(
                    from_npc="Bert",
                    to_npc="Carol",
                    subject="Erskine",
                    content="is guilty",
                )
            ],
            current_turn=2,
        )
        # Lineage: min(carol_trust_in_bert=0.9, bert_stored_confidence=0.7) = 0.7,
        # then decay 0.2 = 0.5. Strict equality pins the lineage math so a
        # rounding-bug refactor returning 0.69 is caught.
        assert r2.outcomes[0].credibility_after == pytest.approx(0.5), (
            "Multi-hop A→B→C must read Bert's stored Suspicion confidence (0.7) "
            "as the credibility floor, then decay 0.2 → 0.5. A naive per-call "
            "trust delta (0.9 - 0.2 = 0.7) would pass strict-inequality but "
            "fails this exact pin."
        )


# ---------------------------------------------------------------------------
# AC5 — OTEL observability
#
# The GM panel is the lie detector. Every transmission MUST emit
# SPAN_GOSSIP_PROPAGATION with credibility_before / credibility_after /
# accepted attributes. Belief-state mutations MUST emit
# SPAN_BELIEF_STATE_MUTATION. Contradiction outcomes MUST be visible —
# never silently dropped (CLAUDE.md: "No Silent Fallbacks").
# ---------------------------------------------------------------------------


class TestOtelObservability:
    def test_propagate_emits_gossip_span_per_transmission(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        from sidequest.telemetry.spans import SPAN_GOSSIP_PROPAGATION

        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.6)

        engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="is guilty",
                ),
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="was seen leaving",
                ),
            ],
            current_turn=1,
        )

        spans = _spans_named(otel_capture, SPAN_GOSSIP_PROPAGATION)
        assert len(spans) == 2, (
            f"Expected one gossip span per transmission (2); got {len(spans)} "
            f"in {[s.name for s in otel_capture.get_finished_spans()]}"
        )
        # AC3, AC5: pin specific values, not just attribute presence, so a
        # regression that emits accepted=None or wrong credibility numbers is
        # caught. Test setup: trust=0.6, default decay=0.1 → before=0.6,
        # after=0.5. accepted=True (cred_after > 0 and not contradicted).
        attrs = dict(spans[0].attributes or {})
        assert attrs["credibility_before"] == pytest.approx(0.6)
        assert attrs["credibility_after"] == pytest.approx(0.5)
        assert attrs["accepted"] is True
        assert attrs["from_npc"] == "Alice"
        assert attrs["to_npc"] == "Bert"
        assert attrs["subject"] == "Erskine"

    def test_belief_state_mutation_span_fires_when_receiver_updates(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        from sidequest.telemetry.spans import SPAN_BELIEF_STATE_MUTATION

        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)

        engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="left at 9pm",
                )
            ],
            current_turn=1,
        )

        # Pin exact count + exact attribute values. A bare truthy check
        # (assert mutation_spans) lets a regression emit any non-empty list
        # past; the GM panel needs the precise NPC, subject, and confidence
        # the engine actually wrote. Setup: trust=0.9, default decay=0.1 →
        # confidence=0.8.
        mutation_spans = _spans_named(otel_capture, SPAN_BELIEF_STATE_MUTATION)
        assert len(mutation_spans) == 1
        attrs = dict(mutation_spans[0].attributes or {})
        assert attrs["npc"] == "Bert"
        assert attrs["subject"] == "Erskine"
        assert attrs["confidence"] == pytest.approx(0.8)
        assert attrs["contradicted"] is False

    def test_mutation_span_nested_inside_propagation_span(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """The mutation span must be a child of the propagation span. The
        engine docstring advertises this nesting; without a parent-id
        assertion, a refactor that un-nests the spans would pass every
        existence test while breaking the GM panel's trace hierarchy.
        """
        from sidequest.telemetry.spans import (
            SPAN_BELIEF_STATE_MUTATION,
            SPAN_GOSSIP_PROPAGATION,
        )

        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.9)

        engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="left at 9pm",
                )
            ],
            current_turn=1,
        )

        propagation_spans = _spans_named(otel_capture, SPAN_GOSSIP_PROPAGATION)
        mutation_spans = _spans_named(otel_capture, SPAN_BELIEF_STATE_MUTATION)
        assert len(propagation_spans) == 1
        assert len(mutation_spans) == 1
        # Parent context of the mutation span must be the propagation span.
        # OTEL exposes the parent on .parent (a SpanContext) — its span_id
        # must equal the propagation span's context.span_id.
        assert mutation_spans[0].parent is not None, (
            "Mutation span must have a parent — the propagation span. None "
            "indicates the mutation span was opened outside the propagation "
            "span's `with` block."
        )
        assert mutation_spans[0].parent.span_id == propagation_spans[0].context.span_id, (
            "Mutation span's parent must be the propagation span. The engine "
            "docstring claims this nesting; if it ever stops holding, the GM "
            "panel's trace hierarchy breaks silently."
        )

    def test_contradiction_outcome_is_visible_not_silently_dropped(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """AC5: residual contradictions logged; nothing dropped silently."""
        from sidequest.telemetry.spans import SPAN_GOSSIP_PROPAGATION

        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.7)
        bert.add_belief(
            BeliefFact(
                subject="Erskine",
                content="Was at the pub",
                turn_learned=1,
                source=BeliefSourceWitnessed(),
            )
        )

        result = engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="Erskine",
                    content="Was in the library",
                    sentiment="contradicting",
                )
            ],
            current_turn=2,
        )

        # Outcome surfaces the contradiction.
        assert result.outcomes[0].contradicted is True

        # And the span records it.
        spans = _spans_named(otel_capture, SPAN_GOSSIP_PROPAGATION)
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        assert attrs.get("contradicted") is True, (
            f"Contradiction must surface on the propagation span attrs; got {attrs}. "
            "Silent fallback (storing the gossip with no contradicted flag) is forbidden."
        )

    def test_rejected_gossip_still_emits_span_but_no_mutation(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """When accepted=False (credibility_after clamped to 0), the propagation
        span fires (rejection is observable) but the mutation span does NOT
        — there was no belief-state change to observe. Pinning both halves
        catches a regression that always emits a mutation span or one that
        silently drops the propagation span on rejection.
        """
        from sidequest.telemetry.spans import (
            SPAN_BELIEF_STATE_MUTATION,
            SPAN_GOSSIP_PROPAGATION,
        )

        engine = GossipEngine(decay_per_hop=0.4)
        bert = BeliefState()
        bert.update_credibility("Alice", 0.1)  # below any reasonable threshold post-decay

        engine.propagate(
            npcs={"Alice": BeliefState(), "Bert": bert},
            transmissions=[
                GossipTransmission(
                    from_npc="Alice",
                    to_npc="Bert",
                    subject="X",
                    content="Y",
                )
            ],
            current_turn=1,
        )

        propagation_spans = _spans_named(otel_capture, SPAN_GOSSIP_PROPAGATION)
        assert len(propagation_spans) == 1, "Rejection must still emit a propagation span"
        attrs = dict(propagation_spans[0].attributes or {})
        assert attrs["accepted"] is False
        assert attrs["credibility_after"] == pytest.approx(0.0)

        # No mutation span — the receiver's belief_state was not modified.
        mutation_spans = _spans_named(otel_capture, SPAN_BELIEF_STATE_MUTATION)
        assert mutation_spans == [], (
            f"Rejected gossip must NOT emit a belief-state mutation span (the "
            f"receiver's beliefs did not change). Got {len(mutation_spans)} "
            f"mutation span(s)."
        )

        # Bert's belief_state should be untouched.
        assert bert.beliefs_about("X") == []


# ---------------------------------------------------------------------------
# Rule-coverage tests — lang-review checklist enforcement
#
# Python rules #1 (silent exceptions / fallbacks), #6 (test quality
# self-check), #11 (input validation at boundaries). CLAUDE.md: "No silent
# fallbacks" — bad input MUST raise, not be swallowed.
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_transmission_rejects_empty_subject(self) -> None:
        """Empty subject is structurally invalid; constructor must raise."""
        with pytest.raises(ValidationError):
            GossipTransmission(
                from_npc="Alice",
                to_npc="Bert",
                subject="",
                content="non-empty",
            )

    def test_transmission_rejects_empty_content(self) -> None:
        with pytest.raises(ValidationError):
            GossipTransmission(
                from_npc="Alice",
                to_npc="Bert",
                subject="Erskine",
                content="",
            )

    def test_transmission_rejects_empty_from_npc(self) -> None:
        with pytest.raises(ValidationError):
            GossipTransmission(
                from_npc="",
                to_npc="Bert",
                subject="Erskine",
                content="x",
            )

    def test_transmission_rejects_self_loop(self) -> None:
        """An NPC cannot gossip to themselves — structurally meaningless."""
        with pytest.raises(ValidationError):
            GossipTransmission(
                from_npc="Alice",
                to_npc="Alice",
                subject="Erskine",
                content="x",
            )

    def test_propagate_rejects_unknown_to_npc(self) -> None:
        """No silent fallback: targeting an NPC absent from the npcs map
        raises ``KeyError``, rather than being skipped silently. Tightened
        from ``(KeyError, ValueError)`` to a single specific type — the
        engine's documented contract is KeyError; a regression that
        substitutes ValueError must surface, not pass."""
        engine = GossipEngine()
        with pytest.raises(KeyError):
            engine.propagate(
                npcs={"Alice": BeliefState()},
                transmissions=[
                    GossipTransmission(
                        from_npc="Alice",
                        to_npc="Ghost",
                        subject="x",
                        content="y",
                    )
                ],
                current_turn=0,
            )

    def test_propagate_rejects_unknown_from_npc(self) -> None:
        """No silent fallback: ``from_npc`` absent from the npcs map MUST
        raise ``KeyError``, symmetric to the ``to_npc`` check. Silent
        acceptance of an unknown sender is exactly the pattern CLAUDE.md's
        'No Silent Fallbacks' forbids — the engine's own docstring self-
        cites the rule. A receiver's default 0.5 credibility-for-strangers
        masks a typo or stale-name caller bug; the engine refuses to
        propagate gossip whose source is not in the registered NPC map.
        """
        engine = GossipEngine()
        bert = BeliefState()
        bert.update_credibility("Alice", 0.5)
        with pytest.raises(KeyError):
            engine.propagate(
                npcs={"Bert": bert},  # Alice absent — caller bug or stale name
                transmissions=[
                    GossipTransmission(
                        from_npc="Alice",
                        to_npc="Bert",
                        subject="x",
                        content="y",
                    )
                ],
                current_turn=0,
            )


# ---------------------------------------------------------------------------
# Wiring test — engine must be reachable from the game namespace consumers
# can import from, not just buried in an unexported submodule.
# CLAUDE.md: "Every Test Suite Needs a Wiring Test."
# ---------------------------------------------------------------------------


class TestWiring:
    def test_gossip_engine_exported_from_module(self) -> None:
        """The engine must be importable from its canonical module path AND
        listed in __all__ so star-imports + IDE discovery work."""
        import sidequest.game.gossip_engine as ge

        assert hasattr(ge, "GossipEngine")
        assert hasattr(ge, "GossipTransmission")
        assert hasattr(ge, "GossipResult")
        assert hasattr(ge, "TransmissionOutcome")
        # __all__ catches the "Dev shipped it but forgot to export" bug.
        assert "GossipEngine" in getattr(ge, "__all__", []), (
            "GossipEngine must appear in __all__ — consumers import via star"
        )

    def test_gossip_span_constants_registered(self) -> None:
        """The two new spans must be registered in the telemetry catalog —
        otherwise tests/telemetry/test_routing_completeness.py will trip and
        the GM panel will see unrouted span names."""
        from sidequest.telemetry.spans import (
            FLAT_ONLY_SPANS,
            SPAN_BELIEF_STATE_MUTATION,
            SPAN_GOSSIP_PROPAGATION,
            SPAN_ROUTES,
        )

        # Each new span must be either routed to a typed event OR explicitly
        # flat-only. Membership-in-neither is the bug the registry catches.
        assert (
            SPAN_GOSSIP_PROPAGATION in FLAT_ONLY_SPANS or SPAN_GOSSIP_PROPAGATION in SPAN_ROUTES
        ), "SPAN_GOSSIP_PROPAGATION must be registered (flat-only or routed)"
        assert (
            SPAN_BELIEF_STATE_MUTATION in FLAT_ONLY_SPANS
            or SPAN_BELIEF_STATE_MUTATION in SPAN_ROUTES
        ), "SPAN_BELIEF_STATE_MUTATION must be registered (flat-only or routed)"
