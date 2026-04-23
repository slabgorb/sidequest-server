"""Genre pack loader picks up projection.yaml when present."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sidequest.genre.loader import load_genre_pack

# Resolve the real caverns_and_claudes pack directory. Must match the pattern
# used by tests/agents/test_orchestrator_e2e.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]  # sidequest-server → oq-2
CAVERNS_PACK_DIR = _REPO_ROOT / "sidequest-content" / "genre_packs" / "caverns_and_claudes"


def _clone_pack(src: Path, dst: Path) -> Path:
    """Deep-copy a pack so the test can mutate the copy safely."""
    shutil.copytree(src, dst)
    return dst


def test_pack_without_projection_yaml_has_projection_rules_none(tmp_path: Path) -> None:
    # caverns_and_claudes in the source tree has no projection.yaml.
    pack = load_genre_pack(CAVERNS_PACK_DIR)
    assert pack.projection_rules is None


def test_pack_with_projection_yaml_loads_rules(tmp_path: Path) -> None:
    pack_dir = _clone_pack(CAVERNS_PACK_DIR, tmp_path / "caverns")
    (pack_dir / "projection.yaml").write_text(
        """rules:
  - kind: NARRATION
    redact_fields:
      - field: text
        unless: is_gm()
        mask: null
"""
    )
    pack = load_genre_pack(pack_dir)
    assert pack.projection_rules is not None
    assert len(pack.projection_rules.rules) == 1


def test_invalid_projection_yaml_fails_pack_load(tmp_path: Path) -> None:
    pack_dir = _clone_pack(CAVERNS_PACK_DIR, tmp_path / "caverns_bad")
    (pack_dir / "projection.yaml").write_text(
        """rules:
  - kind: NOT_A_REAL_KIND
    target_only:
      field: to
"""
    )
    with pytest.raises(Exception, match="unknown kind"):
        load_genre_pack(pack_dir)


def test_source_dir_is_set_on_loaded_pack() -> None:
    pack = load_genre_pack(CAVERNS_PACK_DIR)
    assert pack.source_dir == CAVERNS_PACK_DIR
