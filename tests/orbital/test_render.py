"""Renderer tests — SVG output structure for the engraved layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital.loader import load_orbital_content
from sidequest.orbital.render import Scope, render_chart

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def world_minimal():
    return load_orbital_content(FIXTURES / "world_minimal")


def test_render_returns_valid_svg(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert svg.startswith("<?xml") or svg.startswith("<svg")
    assert "</svg>" in svg


def test_render_has_engraved_layer(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert 'id="layer-engraved"' in svg


def test_engraved_layer_has_orbits_for_each_orbiting_body(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert 'data-body-id="red_prospect"' in svg


def test_engraved_layer_has_named_bodies(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert "COYOTE" in svg
    assert "RED PROSPECT" in svg


def test_render_deterministic_for_same_inputs(world_minimal):
    svg1 = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    svg2 = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert svg1 == svg2


def test_t_hours_changes_body_position(world_minimal):
    """Bodies move with time — the rendered position differs at different t."""
    svg_t0 = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    # 1000h ≈ 0.11 of red_prospect's 9120h orbital period — small but real shift.
    svg_t_later = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=1000.0,
        party_at=None,
    )
    assert svg_t0 != svg_t_later


def test_flavor_layer_present_when_annotations_exist(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert 'id="layer-flavor"' in svg
    assert "absent gate" in svg or "?" in svg


def test_engraved_label_text_appears(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert "the Last Drift" in svg


def test_party_marker_renders_at_body(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at="turning_hub",
    )
    assert 'id="layer-party"' in svg
    assert 'data-party-at="turning_hub"' in svg


def test_party_marker_absent_when_party_at_none(world_minimal):
    svg = render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert "data-party-at=" not in svg


def test_render_emits_chart_render_span(world_minimal, otel_capture):
    """Per spec §7.3 — every render emits chart.render with scope/t/party/size."""
    render_chart(
        orbits=world_minimal.orbits,
        chart=world_minimal.chart,
        scope=Scope.system_root(),
        t_hours=24.0,
        party_at="turning_hub",
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "chart.render"]
    assert len(spans) == 1, (
        f"expected 1 chart.render span, got {len(spans)}; "
        f"all spans: {[s.name for s in otel_capture.get_finished_spans()]}"
    )
    s = spans[0]
    assert s.attributes["scope_center"] == "coyote"
    assert s.attributes["t_hours"] == 24.0
    assert s.attributes["party_at"] == "turning_hub"
    assert s.attributes["body_count"] == 3
    assert s.attributes["output_size_bytes"] > 0
