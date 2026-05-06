"""Failing tests for the new ``snapshot.party_location_query`` OTEL span
(story 45-48 / Wave 2B / S3 / AC7).

Per design doc § "OTEL — the lie-detector wiring" (lines 282-286):

    Was there a party-location split this turn?
    span: snapshot.party_location_query
    attributes: perspective_supplied, consensus_found, party_split

The span is emitted by ``GameSnapshot.party_location()`` so Sebastien's GM
panel can see when the party is mechanically split (consensus call returned
None) and per-character headers might disagree.
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

from sidequest.game.session import GameSnapshot
from sidequest.telemetry.setup import init_tracer


@pytest.fixture
def otel_capture() -> Iterator[InMemorySpanExporter]:
    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def _query_spans(exporter: InMemorySpanExporter) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == "snapshot.party_location_query"]


# ---------------------------------------------------------------------------
# Span constant is exported
# ---------------------------------------------------------------------------


def test_span_constant_exists_in_telemetry_package() -> None:
    """The constant must be importable from ``sidequest.telemetry.spans`` so
    callers don't string-literal the name."""
    from sidequest.telemetry import spans

    assert hasattr(spans, "SPAN_PARTY_LOCATION_QUERY"), (
        "SPAN_PARTY_LOCATION_QUERY missing — Wave 2B AC7"
    )
    assert spans.SPAN_PARTY_LOCATION_QUERY == "snapshot.party_location_query"


# ---------------------------------------------------------------------------
# Span fires from each accessor mode
# ---------------------------------------------------------------------------


def test_party_location_emits_span_with_perspective_supplied(otel_capture) -> None:
    snap = GameSnapshot(character_locations={"Shirley": "Cockpit"})
    snap.party_location(perspective="Shirley")

    spans = _query_spans(otel_capture)
    assert spans, "snapshot.party_location_query span did not fire"
    attrs = dict(spans[-1].attributes or {})
    assert attrs.get("perspective_supplied") is True
    # When perspective is given, the consensus/split fields describe that
    # short-circuit (consensus computation skipped); they should still be
    # present so the GM panel sees a uniform schema.
    assert "consensus_found" in attrs
    assert "party_split" in attrs


def test_party_location_consensus_emits_span_with_consensus_found(otel_capture) -> None:
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Galley", "Laverne": "Galley"},
    )
    snap.party_location()

    spans = _query_spans(otel_capture)
    assert spans
    attrs = dict(spans[-1].attributes or {})
    assert attrs.get("perspective_supplied") is False
    assert attrs.get("consensus_found") is True
    assert attrs.get("party_split") is False


def test_party_location_split_emits_span_with_party_split_true(otel_capture) -> None:
    """The lie-detector signal: this span fires when the party can't agree."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Cockpit", "Laverne": "Galley"},
    )
    snap.party_location()

    spans = _query_spans(otel_capture)
    assert spans
    attrs = dict(spans[-1].attributes or {})
    assert attrs.get("perspective_supplied") is False
    assert attrs.get("consensus_found") is False
    assert attrs.get("party_split") is True


def test_party_location_no_seated_pcs_does_not_count_as_split(otel_capture) -> None:
    """No seated PCs → not a 'split', just unset. The GM panel should not
    flag this as a party-split incident — that would noise up the lie
    detector at session start before chargen completes."""
    snap = GameSnapshot(player_seats={}, character_locations={})
    snap.party_location()

    spans = _query_spans(otel_capture)
    assert spans
    attrs = dict(spans[-1].attributes or {})
    assert attrs.get("party_split") is False
