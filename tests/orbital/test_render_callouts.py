"""End-to-end and unit tests for ADR-094 orrery callouts.

Spec: docs/superpowers/specs/2026-05-04-adr-094-orrery-callouts-implementation-design.md
ADR:  docs/adr/094-orrery-label-placement-strategies.md

This file pins the §6.2 acceptance criteria from the spec. Pure-logic
unit tests for label_strategy live in test_label_strategy.py; this file
covers the renderer integration end-to-end via render_chart() against
the world_callout_strategy fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital.loader import load_orbital_content

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOTS = Path(__file__).parent / "snapshots"


@pytest.fixture
def world_callout_strategy():
    """The synthetic fixture exercising every selection rule."""
    return load_orbital_content(FIXTURES / "world_callout_strategy")


class TestMoonBandForcedCalloutSurface:
    def test_moon_band_children_with_labels_surface_to_strategy(
        self, world_callout_strategy
    ):
        # Render at system scope. Companion-children (habitat_x1..x3) and
        # the habitat-moons (moon_z1, moon_z2) all have labels and should
        # appear in chart.label_distribution.bodies_callout.
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0,
            party_at=None,
        )
        # Spot-check the callout block is present in SVG output.
        assert "<g class=\"moon-band\"" in svg or "class=\"moon-band\"" in svg
        assert "HABITAT X-1" in svg
        assert "MOON Z-1" in svg


class TestStrategyDispatch:
    """End-to-end via render_chart against world_callout_strategy fixture."""

    def test_outer_world_renders_textpath(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        assert "<textPath" in svg
        assert "OUTER WORLD" in svg

    @pytest.mark.xfail(reason="Task 23 implements callout SVG emission")
    def test_spread_alpha_renders_callout_via_explicit(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        assert "SPREAD ALPHA" in svg
        assert "habitat · 3.0 AU" in svg

    @pytest.mark.xfail(reason="Task 23 implements callout SVG emission")
    def test_companion_children_grouped_block(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        assert "COMPANION DWARF SYSTEM" in svg
        assert "HABITAT X-1" in svg
        assert "HABITAT X-2" in svg
        assert "HABITAT X-3" in svg

    def test_lonely_companion_singleton_callout(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        assert "HABITAT Y-1" in svg
        assert "LONELY COMPANION SYSTEM" not in svg

    def test_habitat_with_moons_grouping(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        assert "MOON Z-1" in svg
        assert "MOON Z-2" in svg
        assert "HABITAT WITH MOONS SYSTEM" not in svg


class TestEmitTextpathLabel:
    def test_textpath_uses_resolved_path_id(self, world_callout_strategy):
        from sidequest.orbital.render import Scope, render_chart
        svg = render_chart(
            orbits=world_callout_strategy.orbits,
            chart=world_callout_strategy.chart,
            scope=Scope.system_root(),
            t_hours=0.0, party_at=None,
        )
        # _resolve_curve_along's path id convention is `curve_orbit_<body_id>`
        # for orbit references (per render._resolve_curve_along).
        assert 'href="#curve_orbit_outer_world"' in svg or \
               'xlink:href="#curve_orbit_outer_world"' in svg
        assert "— OUTER WORLD —" in svg
