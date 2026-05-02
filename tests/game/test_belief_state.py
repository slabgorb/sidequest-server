"""Tests for ``sidequest.game.belief_state`` — Story 2.3 Slice D.

Covers data-model behavior and OTEL watcher events emitted on mutation.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.belief_state import (
    BeliefClaim,
    BeliefFact,
    BeliefSourceInferred,
    BeliefSourceOverheard,
    BeliefSourceToldBy,
    BeliefSourceWitnessed,
    BeliefState,
    BeliefSuspicion,
    Credibility,
)

# ---------------------------------------------------------------------------
# OTEL harness (mirrors tests/server/test_chargen_summary.py)
# ---------------------------------------------------------------------------


def _fresh_otel() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _capture_events(provider: TracerProvider, fn) -> list:  # type: ignore[no-untyped-def]
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("test_harness"):
        fn()
    for processor in provider._active_span_processor._span_processors:  # type: ignore[attr-defined]
        if isinstance(processor, SimpleSpanProcessor):
            inner = processor.span_exporter  # type: ignore[attr-defined]
            if isinstance(inner, InMemorySpanExporter):
                finished = inner.get_finished_spans()
                assert finished, "no span was exported"
                return list(finished[-1].events)
    raise AssertionError("no InMemorySpanExporter found on provider")


# ---------------------------------------------------------------------------
# Belief mutation & query
# ---------------------------------------------------------------------------


class TestAddBelief:
    def test_add_fact_appends_and_queries_by_subject(self) -> None:
        bs = BeliefState()
        fact = BeliefFact(
            subject="Erskine",
            content="Was in the library at 9pm",
            turn_learned=3,
            source=BeliefSourceWitnessed(),
        )
        bs.add_belief(fact)

        assert len(bs.beliefs) == 1
        results = bs.beliefs_about("Erskine")
        assert len(results) == 1
        assert results[0].content == "Was in the library at 9pm"

    def test_add_suspicion_stores_confidence(self) -> None:
        bs = BeliefState()
        bs.add_belief(
            BeliefSuspicion.make(
                subject="Erskine",
                content="Looked shifty",
                turn_learned=5,
                source=BeliefSourceInferred(),
                confidence=0.7,
            )
        )
        stored = bs.beliefs_about("Erskine")[0]
        assert isinstance(stored, BeliefSuspicion)
        assert stored.confidence == 0.7

    def test_suspicion_clamps_confidence_high_and_low(self) -> None:
        high = BeliefSuspicion.make(
            subject="x",
            content="y",
            turn_learned=0,
            source=BeliefSourceInferred(),
            confidence=1.7,
        )
        low = BeliefSuspicion.make(
            subject="x",
            content="y",
            turn_learned=0,
            source=BeliefSourceInferred(),
            confidence=-0.3,
        )
        assert high.confidence == 1.0
        assert low.confidence == 0.0

    def test_add_claim_records_sentiment_and_believed(self) -> None:
        bs = BeliefState()
        claim = BeliefClaim(
            subject="Erskine",
            content="Said he was at the pub",
            turn_learned=2,
            source=BeliefSourceToldBy(by="Mrs Hoggett"),
            believed=False,
            sentiment="contradicting",
        )
        bs.add_belief(claim)
        stored = bs.beliefs_about("Erskine")[0]
        assert isinstance(stored, BeliefClaim)
        assert stored.believed is False
        assert stored.sentiment == "contradicting"

    def test_beliefs_about_is_subject_scoped(self) -> None:
        bs = BeliefState()
        bs.add_belief(
            BeliefFact(subject="A", content="c1", turn_learned=0, source=BeliefSourceWitnessed())
        )
        bs.add_belief(
            BeliefFact(subject="B", content="c2", turn_learned=0, source=BeliefSourceWitnessed())
        )
        assert len(bs.beliefs_about("A")) == 1
        assert len(bs.beliefs_about("C")) == 0


# ---------------------------------------------------------------------------
# Credibility
# ---------------------------------------------------------------------------


class TestCredibility:
    def test_default_is_half(self) -> None:
        bs = BeliefState()
        assert bs.credibility_of("unknown").score == 0.5

    def test_update_stores_clamped_value(self) -> None:
        bs = BeliefState()
        bs.update_credibility("Ada", 1.4)
        bs.update_credibility("Bert", -0.2)
        assert bs.credibility_of("Ada").score == 1.0
        assert bs.credibility_of("Bert").score == 0.0

    def test_update_overwrites_previous(self) -> None:
        bs = BeliefState()
        bs.update_credibility("Ada", 0.3)
        bs.update_credibility("Ada", 0.8)
        assert bs.credibility_of("Ada").score == 0.8

    def test_credibility_adjust_clamps(self) -> None:
        c = Credibility.new(0.9)
        c.adjust(0.5)
        assert c.score == 1.0
        c.adjust(-2.0)
        assert c.score == 0.0

    def test_credibility_of_returns_copy(self) -> None:
        bs = BeliefState()
        bs.update_credibility("Ada", 0.4)
        got = bs.credibility_of("Ada")
        got.score = 0.0
        # Stored value unaffected
        assert bs.credibility_of("Ada").score == 0.4


# ---------------------------------------------------------------------------
# OTEL events
# ---------------------------------------------------------------------------


class TestOtelEvents:
    def test_add_belief_emits_watcher_event(self) -> None:
        provider, _ = _fresh_otel()

        def work() -> None:
            bs = BeliefState()
            tracer = provider.get_tracer("inner")
            with tracer.start_as_current_span("belief_op"):
                bs.add_belief(
                    BeliefFact(
                        subject="Erskine",
                        content="saw smoke",
                        turn_learned=1,
                        source=BeliefSourceOverheard(),
                    )
                )

        events = _capture_events(provider, work)
        # Events land on the innermost span — find the belief event.
        names = [e.name for e in events]
        # With nested spans, _capture_events returns the outer span's
        # events; the belief event belongs to the inner span. Scan all
        # finished spans instead.
        provider2, exporter2 = _fresh_otel()
        tracer = provider2.get_tracer("t")
        with tracer.start_as_current_span("outer"):
            bs = BeliefState()
            bs.add_belief(
                BeliefFact(
                    subject="Erskine",
                    content="saw smoke",
                    turn_learned=1,
                    source=BeliefSourceOverheard(),
                )
            )
        all_events = [e for span in exporter2.get_finished_spans() for e in span.events]
        belief_events = [e for e in all_events if e.name == "belief_state.belief_added"]
        assert belief_events, f"no belief_added event in {[e.name for e in all_events]}"
        attrs = dict(belief_events[0].attributes or {})
        assert attrs["action"] == "belief_added"
        assert attrs["variant"] == "fact"
        assert attrs["subject"] == "Erskine"
        assert attrs["source"] == "overheard"
        assert attrs["beliefs_count_after"] == 1
        del names  # silence unused

    def test_told_by_source_carries_name(self) -> None:
        provider, exporter = _fresh_otel()
        tracer = provider.get_tracer("t")
        with tracer.start_as_current_span("outer"):
            bs = BeliefState()
            bs.add_belief(
                BeliefClaim(
                    subject="A",
                    content="c",
                    turn_learned=0,
                    source=BeliefSourceToldBy(by="Mrs Hoggett"),
                    believed=True,
                    sentiment="neutral",
                )
            )
        events = [
            e
            for span in exporter.get_finished_spans()
            for e in span.events
            if e.name == "belief_state.belief_added"
        ]
        assert events
        assert dict(events[0].attributes or {})["source"] == "told_by:Mrs Hoggett"

    def test_update_credibility_emits_event_with_previous_and_new(self) -> None:
        provider, exporter = _fresh_otel()
        tracer = provider.get_tracer("t")
        with tracer.start_as_current_span("outer"):
            bs = BeliefState()
            bs.update_credibility("Ada", 0.7)
            bs.update_credibility("Ada", 1.2)  # clamp to 1.0

        events = [
            e
            for span in exporter.get_finished_spans()
            for e in span.events
            if e.name == "belief_state.credibility_updated"
        ]
        assert len(events) == 2
        second = dict(events[1].attributes or {})
        assert second["target_npc"] == "Ada"
        assert second["previous_score"] == 0.7
        assert second["requested_score"] == 1.2
        assert second["new_score"] == 1.0


# ---------------------------------------------------------------------------
# Serialization round-trip — saves need to survive the wire
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_all_three_variants_round_trip(self) -> None:
        bs = BeliefState()
        bs.add_belief(
            BeliefFact(
                subject="A",
                content="fa",
                turn_learned=1,
                source=BeliefSourceWitnessed(),
            )
        )
        bs.add_belief(
            BeliefSuspicion.make(
                subject="B",
                content="sa",
                turn_learned=2,
                source=BeliefSourceInferred(),
                confidence=0.5,
            )
        )
        bs.add_belief(
            BeliefClaim(
                subject="C",
                content="ca",
                turn_learned=3,
                source=BeliefSourceToldBy(by="D"),
                believed=True,
                sentiment="corroborating",
            )
        )
        bs.update_credibility("D", 0.8)

        restored = BeliefState.model_validate_json(bs.model_dump_json())
        assert len(restored.beliefs) == 3
        variants = [b.variant for b in restored.beliefs]
        assert variants == ["fact", "suspicion", "claim"]
        assert restored.credibility_scores["D"].score == 0.8
