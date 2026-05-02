"""Scope tests — system scope vs. body scope (drill-in)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital.loader import load_orbital_content
from sidequest.orbital.render import Scope, render_chart

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def world():
    return load_orbital_content(FIXTURES / "world_minimal")


def test_system_scope_renders_drillable_cluster_for_red_prospect(world):
    """System scope: red_prospect (with children) collapses to a cluster glyph
    that carries an explicit drill-in affordance."""
    svg = render_chart(
        orbits=world.orbits,
        chart=world.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    assert 'data-action="drill_in:red_prospect"' in svg


def test_body_scope_centers_on_red_prospect(world):
    svg = render_chart(
        orbits=world.orbits,
        chart=world.chart,
        scope=Scope(center_body_id="red_prospect"),
        t_hours=0.0,
        party_at=None,
    )
    # Direct children rendered: turning_hub
    assert 'data-body-id="turning_hub"' in svg
    # Parent indicator
    assert 'data-action="drill_out"' in svg
    # System primary not rendered as a body inside body scope (only as edge indicator)
    assert "COYOTE SYSTEM" in svg or "← Coyote" in svg


def test_body_scope_unknown_center_raises(world):
    with pytest.raises(ValueError, match="not in bodies"):
        render_chart(
            orbits=world.orbits,
            chart=world.chart,
            scope=Scope(center_body_id="nowhere"),
            t_hours=0.0,
            party_at=None,
        )
