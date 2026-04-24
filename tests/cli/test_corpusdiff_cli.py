from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_corpusdiff_surfaces_round_2_divergence(tmp_path: Path) -> None:
    out = tmp_path / "divergences.json"
    subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusdiff",
         "--save", str(FIXTURES / "per_player_a.db"),
         "--save", str(FIXTURES / "per_player_b.db"),
         "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.read_text())
    rounds = [d["round_number"] for d in data]
    assert 2 in rounds
    assert 1 not in rounds, "round 1 narrator content is identical, should not diverge"


def test_corpusdiff_fails_loud_on_missing_save(tmp_path: Path) -> None:
    out = tmp_path / "divergences.json"
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusdiff",
         "--save", str(tmp_path / "missing.db"),
         "--save", str(FIXTURES / "per_player_b.db"),
         "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_corpusdiff_single_save_emits_empty_array(tmp_path: Path) -> None:
    """One save = nothing to diff against → empty JSON array on disk."""
    out = tmp_path / "divergences.json"
    subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusdiff",
         "--save", str(FIXTURES / "per_player_a.db"),
         "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(out.read_text()) == []
