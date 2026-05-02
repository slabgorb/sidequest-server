"""End-to-end orbital chart flow — view_map / drill_in / drill_out cycle.

Adapted from Task 17 of the orbital-map plan; references to ``Session.empty``,
``Beat``, and ``capture_spans`` are post-port renamings (``Session(snapshot)``,
``StoryBeat``/``StoryBeatKind``, the ``otel_capture`` fixture from
``tests/orbital/conftest.py`` — duplicated here because pytest fixtures don't
cross test packages).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.session import GameSnapshot
from sidequest.orbital.beats import StoryBeat, StoryBeatKind
from sidequest.orbital.intent import handle_orbital_intent
from sidequest.orbital.loader import load_orbital_content
from sidequest.protocol.orbital_intent import OrbitalIntent
from sidequest.server.session import Session
from sidequest.telemetry.setup import init_tracer

ORBITAL_FIXTURES = Path(__file__).resolve().parent.parent / "orbital" / "fixtures" / "world_minimal"


@pytest.fixture
def otel_capture() -> Iterator[InMemorySpanExporter]:
    """Capture spans emitted to the live OTEL tracer provider singleton."""
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


@pytest.fixture
def session() -> Session:
    snapshot = GameSnapshot(party_body_id="turning_hub")
    content = load_orbital_content(ORBITAL_FIXTURES)
    return Session(snapshot, orbital_content=content)


def _spans_named(exporter: InMemorySpanExporter, name: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def test_full_drill_cycle(session, otel_capture):
    # 1. view_map at system root
    r1 = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
    )
    assert r1.scope_center == "coyote"
    assert 'data-action="drill_in:red_prospect"' in r1.svg

    # 2. drill_in to red_prospect
    r2 = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "red_prospect"}),
    )
    assert r2.scope_center == "red_prospect"
    assert 'data-body-id="turning_hub"' in r2.svg
    assert 'data-action="drill_out"' in r2.svg

    # 3. drill_out back to system
    r3 = handle_orbital_intent(session, OrbitalIntent.model_validate({"kind": "drill_out"}))
    assert r3.scope_center == "coyote"
    assert 'data-action="drill_in:red_prospect"' in r3.svg

    # OTEL: each render emits chart.render
    render_spans = _spans_named(otel_capture, "chart.render")
    assert len(render_spans) == 3
    assert [s.attributes["scope_center"] for s in render_spans] == [
        "coyote",
        "red_prospect",
        "coyote",
    ]


def test_clock_advance_visible_in_chart(session, otel_capture):
    """Beat advance moves the clock; next render shows different positions."""
    r_t0 = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
    )
    session.advance_via_beat(StoryBeat(kind=StoryBeatKind.REST, trigger="rest-1"))  # +8h
    session.advance_via_beat(StoryBeat(kind=StoryBeatKind.REST, trigger="rest-2"))  # +8h
    r_t16 = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
    )

    assert r_t0.t_hours == 0.0
    assert r_t16.t_hours == 16.0
    assert r_t0.svg != r_t16.svg

    # Two clock.advance spans, both REST.
    advance_spans = _spans_named(otel_capture, "clock.advance")
    assert len(advance_spans) == 2
    assert all(s.attributes["beat_kind"] == "rest" for s in advance_spans)


def test_drill_in_then_view_map_with_explicit_scope(session, otel_capture):
    """view_map with a body_id scope acts like drill_in (per spec §6.3)."""
    handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "red_prospect"}),
    )
    # view_map with explicit body_id scope re-renders body scope.
    r = handle_orbital_intent(
        session,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "red_prospect"}),
    )
    assert r.scope_center == "red_prospect"

    # session.orbital_scope is updated on every successful intent.
    assert session.orbital_scope.center_body_id == "red_prospect"

    render_spans = _spans_named(otel_capture, "chart.render")
    assert [s.attributes["scope_center"] for s in render_spans] == [
        "red_prospect",
        "red_prospect",
    ]
