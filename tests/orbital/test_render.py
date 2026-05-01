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
