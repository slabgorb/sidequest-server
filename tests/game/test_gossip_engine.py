"""Tests for ``sidequest.game.gossip_engine`` — Story 50-7.

Drives the RED phase for the GossipEngine: two-phase belief propagation,
contradiction detection, credibility decay, and OTEL observability per
ADR-053 (Scenario System).

The module under test does not yet exist; every test in this file fails
at import time until Dev brings the engine online.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import ValidationError

from sidequest.game.belief_state import (
    BeliefFact,
    BeliefSourceWitnessed,
    BeliefState,
)

# Importing the not-yet-existent module up front so the whole suite fails
# until GossipEngine and its dataclasses land. This is intentional RED.
from sidequest.game.gossip_engine import (  # noqa: E402
    GossipEngine,
    GossipResult,
    GossipTransmission,
    TransmissionOutcome,
)

# ---------------------------------------------------------------------------
# OTEL harness — mirrors tests/magic/test_innate_v1_cast_resolution.py
# ---------------------------------------------------------------------------


@pytest.fixture
def otel_capture() -> Iterator[InMemorySpanExporter]:
    """In-memory OTEL exporter installed on the live tracer provider."""
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        "Default tracer provider must be TracerProvider for span capture"
    )
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def _events_named(exporter: InMemorySpanExporter, name: str) -> list:
    """Collect every event whose name matches across all finished spans."""
    return [
        e for span in exporter.get_finished_spans() for e in span.events if e.name == name
    ]


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
        """AC4 — two-NPC chain: A→B updates B's belief state; A unchanged."""
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
        assert facts == [], (
            "Gossip from a low-credibility source must never be stored as Fact"
        )

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
                GossipTransmission(
                    from_npc="Alice", to_npc="Bert", subject="x", content="y"
                )
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
                GossipTransmission(
                    from_npc="Alice", to_npc="Bert", subject="x", content="y"
                )
            ],
            current_turn=0,
        )
        assert result.outcomes[0].credibility_after == pytest.approx(0.0)
        assert result.outcomes[0].credibility_after >= 0.0

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
        assert r2.outcomes[0].credibility_after < 0.7, (
            "Multi-hop A→B→C must decay credibility strictly more than the "
            "single-hop equivalent (0.9 - 0.2 = 0.7). Engine must track "
            "lineage, not just per-call trust delta."
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
        attrs = dict(spans[0].attributes or {})
        # AC3, AC5: required attributes on every gossip span.
        assert "credibility_before" in attrs, "span must carry credibility_before"
        assert "credibility_after" in attrs, "span must carry credibility_after"
        assert "accepted" in attrs, "span must carry accepted bool"
        assert "from_npc" in attrs and "to_npc" in attrs

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

        mutation_spans = _spans_named(otel_capture, SPAN_BELIEF_STATE_MUTATION)
        assert mutation_spans, (
            "SPAN_BELIEF_STATE_MUTATION must fire when a receiver's belief_state "
            "changes due to gossip — without this, the GM panel can't see whether "
            "the engine actually moved any beliefs."
        )
        attrs = dict(mutation_spans[0].attributes or {})
        assert attrs.get("npc") == "Bert" or attrs.get("target_npc") == "Bert", (
            f"belief_state_mutation span must identify the mutated NPC; got {attrs}"
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

    def test_rejected_gossip_still_emits_span(
        self, otel_capture: InMemorySpanExporter
    ) -> None:
        """Even when accepted=False, the span fires — rejection is observable."""
        from sidequest.telemetry.spans import SPAN_GOSSIP_PROPAGATION

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

        spans = _spans_named(otel_capture, SPAN_GOSSIP_PROPAGATION)
        assert len(spans) == 1, "Rejection must still emit a propagation span"


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
        raises, rather than being skipped silently."""
        engine = GossipEngine()
        with pytest.raises((KeyError, ValueError)):
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
            SPAN_GOSSIP_PROPAGATION in FLAT_ONLY_SPANS
            or SPAN_GOSSIP_PROPAGATION in SPAN_ROUTES
        ), "SPAN_GOSSIP_PROPAGATION must be registered (flat-only or routed)"
        assert (
            SPAN_BELIEF_STATE_MUTATION in FLAT_ONLY_SPANS
            or SPAN_BELIEF_STATE_MUTATION in SPAN_ROUTES
        ), "SPAN_BELIEF_STATE_MUTATION must be registered (flat-only or routed)"
