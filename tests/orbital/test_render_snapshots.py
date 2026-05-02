"""Snapshot tests — pin SVG output bytes for canonical inputs."""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital.loader import load_orbital_content
from sidequest.orbital.render import Scope, render_chart

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOTS = Path(__file__).parent / "snapshots"


@pytest.fixture
def world():
    return load_orbital_content(FIXTURES / "world_minimal")


def _normalize(svg: str) -> str:
    """Whitespace-normalize SVG for stable comparison."""
    return "\n".join(line.rstrip() for line in svg.splitlines() if line.strip())


def _compare_or_record(name: str, actual: str, request: pytest.FixtureRequest) -> None:
    snap_path = SNAPSHOTS / f"{name}.svg"
    if not snap_path.exists() or request.config.getoption("--update-snapshots"):
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(actual)
        pytest.skip(f"snapshot recorded: {snap_path}")
    expected = snap_path.read_text()
    assert _normalize(actual) == _normalize(expected), (
        f"SVG snapshot drift for {name}. "
        f"Run with --update-snapshots to refresh after intentional change."
    )


def test_system_scope_t0_no_party(world, request):
    svg = render_chart(
        orbits=world.orbits,
        chart=world.chart,
        scope=Scope.system_root(),
        t_hours=0.0,
        party_at=None,
    )
    _compare_or_record("system_t0_no_party", svg, request)


def test_system_scope_t100h_party_turning_hub(world, request):
    svg = render_chart(
        orbits=world.orbits,
        chart=world.chart,
        scope=Scope.system_root(),
        t_hours=100.0,
        party_at="turning_hub",
    )
    _compare_or_record("system_t100_party_turning_hub", svg, request)


def test_red_prospect_scope_t0(world, request):
    svg = render_chart(
        orbits=world.orbits,
        chart=world.chart,
        scope=Scope(center_body_id="red_prospect"),
        t_hours=0.0,
        party_at=None,
    )
    _compare_or_record("red_prospect_scope_t0", svg, request)
