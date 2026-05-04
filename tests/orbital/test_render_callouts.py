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
