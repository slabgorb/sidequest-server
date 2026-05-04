"""Wiring + Story Y snapshot tests against the live coyote_star campaign world.

Per CLAUDE.md: "Every Test Suite Needs a Wiring Test" — proves the
ADR-094 pipeline is reachable from production code paths. The Story Y
post-content-edit snapshot test lives in TestStoryYSnapshot.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.orbital.loader import load_orbital_content
from sidequest.telemetry import init_tracer


def _coyote_star_path() -> Path:
    env_path = os.environ.get("SIDEQUEST_CONTENT_COYOTE_STAR")
    if env_path:
        return Path(env_path)
    server_root = Path(__file__).resolve().parents[2]  # sidequest-server/
    repo_root = server_root.parent
    return repo_root / "sidequest-content" / "genre_packs" / "space_opera" / "worlds" / "coyote_star"


@pytest.fixture
def coyote_star_world():
    path = _coyote_star_path()
    if not path.exists():
        pytest.skip(f"coyote_star fixture not present at {path}")
    return load_orbital_content(path)


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


def _last_span_attrs(exporter: InMemorySpanExporter, name: str) -> dict:
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert spans, f"no spans named {name!r} captured"
    return dict(spans[-1].attributes)


class TestCoyoteStarWiring:
    def test_renders_without_crash(self, coyote_star_world, otel_capture):
        """AC-W1: production campaign world renders end-to-end."""
        from sidequest.orbital.render import Scope, render_chart
        from sidequest.telemetry.spans.chart import SPAN_CHART_LABEL_DISTRIBUTION

        svg = render_chart(
            orbits=coyote_star_world.orbits,
            chart=coyote_star_world.chart,
            scope=Scope.system_root(),
            t_hours=0.0,
            party_at=None,
        )
        assert svg.startswith("<?xml") or svg.startswith("<svg")
        assert len(svg) > 1000

        a = _last_span_attrs(otel_capture, SPAN_CHART_LABEL_DISTRIBUTION)
        # Sum invariant (AC-O2):
        assert (
            a["bodies_textpath"] + a["bodies_radial"]
            + a["bodies_callout"] + a["bodies_unlabeled"]
            == a["bodies_total"]
        )
