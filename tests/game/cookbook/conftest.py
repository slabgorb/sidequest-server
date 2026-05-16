"""Shared fixture: the real Beneath Sünden cookbook bundle."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.cookbook.loader import CookbookBundle, load_cookbook

WORLD = (
    Path(__file__).parents[4]
    / "sidequest-content/genre_packs/caverns_and_claudes/worlds/beneath_sunden"
)


@pytest.fixture(scope="session")
def bundle() -> CookbookBundle:
    return load_cookbook(WORLD)
