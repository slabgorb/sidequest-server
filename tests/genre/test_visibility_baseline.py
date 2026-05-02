"""Visibility-baseline YAML schema + per-pack defaults (Group G, Task 2).

The decomposer reads these at session init and uses them as the default
VisibilityTag emission when no turn state suggests otherwise. Validation is
strict; unknown subsystem names or fidelity levels raise at pack-load time,
not at runtime — per CLAUDE.md "no silent fallbacks".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.models.visibility import (
    VisibilityBaseline,
    VisibilityOverrides,
    effective_visibility,
    load_baseline,
)
from tests._helpers.genre_paths import GENRE_PACKS_DIR, find_pack_path

SAMPLE_BASELINE = """
tone: secret_heavy
default_visibility:
  npc_agency: all
  stealth_roll_check: actor_only
  lore_reveal: actor_only
status_effect_fidelity:
  blinded:
    visual_only: drop
    audio_only: keep
all_scope: protagonists
"""


def test_baseline_parses():
    baseline = VisibilityBaseline.model_validate_yaml(SAMPLE_BASELINE)
    assert baseline.tone == "secret_heavy"
    assert baseline.default_visibility["npc_agency"] == "all"
    assert baseline.status_effect_fidelity["blinded"]["visual_only"] == "drop"


def test_baseline_rejects_unknown_fidelity():
    bad = SAMPLE_BASELINE.replace("drop", "vaporize")
    with pytest.raises(ValueError, match="vaporize"):
        VisibilityBaseline.model_validate_yaml(bad)


def test_overrides_are_shallow_delta():
    baseline = VisibilityBaseline.model_validate_yaml(SAMPLE_BASELINE)
    overrides = VisibilityOverrides.model_validate_yaml("default_visibility:\n  lore_reveal: all\n")
    effective = effective_visibility(baseline, overrides)
    assert effective.default_visibility["lore_reveal"] == "all"
    assert effective.default_visibility["npc_agency"] == "all"  # unchanged
    assert effective.default_visibility["stealth_roll_check"] == "actor_only"  # unchanged


def test_loader_fails_loudly_on_missing_pack_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "nonexistent" / "visibility_baseline.yaml")


# ---------------------------------------------------------------------------
# Per-pack integration — every shipping pack has a valid baseline
# ---------------------------------------------------------------------------

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content"


@pytest.mark.parametrize(
    "pack",
    [
        "caverns_and_claudes",
        "elemental_harmony",
        "heavy_metal",
        "mutant_wasteland",
        "space_opera",
        "spaghetti_western",
    ],
)
def test_every_shipping_pack_has_valid_baseline(pack):
    path = find_pack_path(pack) / "visibility_baseline.yaml"
    assert path.exists(), f"missing: {path}"
    baseline = load_baseline(path)
    assert baseline.tone in ("broadcast_heavy", "balanced", "secret_heavy")


# ---------------------------------------------------------------------------
# Loader wiring — pack load populates visibility_baseline, fails on missing
# ---------------------------------------------------------------------------


def test_load_genre_pack_populates_visibility_baseline():
    """load_genre_pack must eagerly load visibility_baseline.yaml."""
    from sidequest.genre.loader import load_genre_pack

    pack = load_genre_pack(GENRE_PACKS_DIR / "caverns_and_claudes")
    assert pack.visibility_baseline is not None
    assert pack.visibility_baseline.tone in ("broadcast_heavy", "balanced", "secret_heavy")


def test_load_genre_pack_fails_loudly_on_missing_visibility_baseline(tmp_path):
    """If visibility_baseline.yaml is absent from a production pack load, raise."""
    import shutil

    from sidequest.genre.error import GenreLoadError
    from sidequest.genre.loader import load_genre_pack

    pack_dir = tmp_path / "cc_no_visibility"
    shutil.copytree(GENRE_PACKS_DIR / "caverns_and_claudes", pack_dir)
    (pack_dir / "visibility_baseline.yaml").unlink()
    with pytest.raises((GenreLoadError, FileNotFoundError)):
        load_genre_pack(pack_dir)
