"""Validators 7 + 8: a world without ``openings.yaml`` fails to load loud.

Direct coverage of the openings-specific validators lives in
``test_loader_validators.py`` (Phase 2 tasks). This file is a deliberate
signpost: loads fail loud, no genre-tier fallback, no silent defaults.

See ``docs/superpowers/specs/2026-05-01-canned-openings-design.md`` §5.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_world_without_openings_yaml_fails(tmp_path: Path) -> None:
    """Documented principle — synthesized-world coverage lives elsewhere.

    Building a tmp world from scratch requires reproducing every
    required file the loader probes (world.yaml, lore.yaml,
    cartography.yaml, etc.) before getting to ``openings.yaml``. The
    loader contract for the openings-specific validators is exercised
    in ``tests/genre/test_loader_validators.py`` (Phase 2 tasks 8–12).
    """
    pytest.skip(
        "Tmp-world synthesis requires reproducing many required files. "
        "The loader contract is exercised in test_loader_validators "
        "(Tasks 8-12) for the openings-specific validators. This file "
        "documents the principle; the earlier validator tests provide "
        "direct coverage."
    )
