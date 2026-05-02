"""Loader tests — reads orbits.yaml and chart.yaml from a world directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.orbital.loader import (
    OrbitalContentMissingError,
    load_orbital_content,
)
from sidequest.orbital.models import BodyType, TravelRealism

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_world_minimal():
    content = load_orbital_content(FIXTURES / "world_minimal")
    assert content.orbits.version == "0.1.0"
    assert content.orbits.travel.realism == TravelRealism.ORBITAL
    assert "coyote" in content.orbits.bodies
    assert content.orbits.bodies["coyote"].type == BodyType.STAR
    assert len(content.chart.annotations) == 2


def test_orbital_tier_missing_file_fails_loudly(tmp_path):
    """An `orbital`-tier world must have orbits.yaml; missing = loud error."""
    (tmp_path / "chart.yaml").write_text("version: '0.1.0'\nannotations: []\n")
    with pytest.raises(OrbitalContentMissingError, match="orbits.yaml"):
        load_orbital_content(tmp_path)


def test_chart_optional(tmp_path):
    """chart.yaml is optional — a world can ship orbits without flavor."""
    (tmp_path / "orbits.yaml").write_text(
        FIXTURES.joinpath("world_minimal/orbits.yaml").read_text()
    )
    content = load_orbital_content(tmp_path)
    assert content.chart.annotations == []


def test_validation_error_propagates(tmp_path):
    """Schema errors surface with body context per No Silent Fallbacks."""
    (tmp_path / "orbits.yaml").write_text(
        """
version: "0.1.0"
clock: {epoch_days: 0}
travel: {realism: orbital}
bodies:
  ghost_moon:
    type: habitat
    parent: never_existed
    semi_major_au: 0.04
    period_days: 6
    epoch_phase_deg: 0
"""
    )
    with pytest.raises(Exception, match="unknown parent"):
        load_orbital_content(tmp_path)
