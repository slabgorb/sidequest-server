"""Tests for loader.py reading openings.yaml + npcs.yaml at world tier."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.loader import (
    GenreLoader,  # noqa: F401  (used by skipped placeholders; activated in Tasks 8-12)
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def test_world_with_openings_loads(tmp_path: Path) -> None:
    """A world that ships openings.yaml + npcs.yaml loads cleanly."""
    pytest.skip("requires Coyote Star content (Phase 6) — placeholder")


def test_world_without_openings_yaml_fails_loud() -> None:
    """Validator: openings.yaml is mandatory at world tier."""
    pytest.skip("requires synthetic world fixture — see Task 11")


def test_npcs_yaml_optional() -> None:
    """A world without npcs.yaml loads (empty authored_npcs list)."""
    pytest.skip("requires synthetic world fixture — see Task 11")
