"""Tests for ADR-094 chart.label_strategy and chart.label_distribution spans."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.telemetry import init_tracer
from sidequest.telemetry.spans._core import FLAT_ONLY_SPANS
from sidequest.telemetry.spans.chart import (
    SPAN_CHART_LABEL_DISTRIBUTION,
    SPAN_CHART_LABEL_STRATEGY,
    emit_chart_label_distribution,
    emit_chart_label_strategy,
)


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


def _last_attrs(exporter: InMemorySpanExporter, name: str) -> dict:
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert spans, f"no spans named {name!r} captured"
    return dict(spans[-1].attributes)


class TestSpanRegistration:
    def test_label_strategy_in_flat_only(self):
        assert SPAN_CHART_LABEL_STRATEGY in FLAT_ONLY_SPANS

    def test_label_distribution_in_flat_only(self):
        assert SPAN_CHART_LABEL_DISTRIBUTION in FLAT_ONLY_SPANS

    def test_span_names(self):
        assert SPAN_CHART_LABEL_STRATEGY == "chart.label_strategy"
        assert SPAN_CHART_LABEL_DISTRIBUTION == "chart.label_distribution"


class TestEmitChartLabelStrategy:
    def test_textpath_decision_emits_clean(self, otel_capture):
        emit_chart_label_strategy(
            body_id="body_x",
            parent_id="parent_a",
            parent_type="habitat",
            strategy_chosen="textpath",
            selection_reason="textpath_fits",
            tier=None,
            arc_available_px=None,
            text_width_px=50.0,
            path_circumference_px=200.0,
        )
        attrs = _last_attrs(otel_capture, SPAN_CHART_LABEL_STRATEGY)
        assert attrs["body_id"] == "body_x"
        assert attrs["parent_id"] == "parent_a"
        assert attrs["strategy_chosen"] == "textpath"
        assert attrs["selection_reason"] == "textpath_fits"
        assert attrs["tier"] == -1
        assert attrs["arc_available_px"] == -1.0
        assert attrs["path_circumference_px"] == 200.0

    def test_radial_decision_emits_tier_and_arc(self, otel_capture):
        emit_chart_label_strategy(
            body_id="body_y",
            parent_id=None,
            parent_type=None,
            strategy_chosen="radial",
            selection_reason="radial_fits",
            tier=2,
            arc_available_px=180.0,
            text_width_px=42.0,
            path_circumference_px=None,
        )
        attrs = _last_attrs(otel_capture, SPAN_CHART_LABEL_STRATEGY)
        assert attrs["parent_id"] == ""
        assert attrs["tier"] == 2
        assert attrs["arc_available_px"] == 180.0
        assert attrs["path_circumference_px"] == -1.0


class TestEmitChartLabelDistribution:
    def test_basic_distribution(self, otel_capture):
        emit_chart_label_distribution(
            bodies_total=10,
            bodies_textpath=2,
            bodies_radial=3,
            bodies_callout=4,
            bodies_unlabeled=1,
            gutter_inset_fallbacks=0,
            cross_group_crossings=0,
        )
        attrs = _last_attrs(otel_capture, SPAN_CHART_LABEL_DISTRIBUTION)
        assert attrs["bodies_total"] == 10
        assert attrs["bodies_textpath"] == 2
        assert attrs["bodies_radial"] == 3
        assert attrs["bodies_callout"] == 4
        assert attrs["bodies_unlabeled"] == 1

    def test_sum_invariant_holds(self, otel_capture):
        # AC-O2: bodies_textpath + bodies_radial + bodies_callout + bodies_unlabeled == bodies_total
        emit_chart_label_distribution(
            bodies_total=10, bodies_textpath=2, bodies_radial=3,
            bodies_callout=4, bodies_unlabeled=1,
            gutter_inset_fallbacks=0, cross_group_crossings=0,
        )
        a = _last_attrs(otel_capture, SPAN_CHART_LABEL_DISTRIBUTION)
        assert (
            a["bodies_textpath"] + a["bodies_radial"]
            + a["bodies_callout"] + a["bodies_unlabeled"]
            == a["bodies_total"]
        )

    def test_with_warnings(self, otel_capture):
        emit_chart_label_distribution(
            bodies_total=20, bodies_textpath=2, bodies_radial=8,
            bodies_callout=10, bodies_unlabeled=0,
            gutter_inset_fallbacks=2,
            cross_group_crossings=1,
        )
        a = _last_attrs(otel_capture, SPAN_CHART_LABEL_DISTRIBUTION)
        assert a["gutter_inset_fallbacks"] == 2
        assert a["cross_group_crossings"] == 1
