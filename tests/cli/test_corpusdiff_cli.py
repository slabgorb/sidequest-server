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


def test_corpusdiff_requires_at_least_two_saves(tmp_path: Path) -> None:
    """One save = nothing to diff → rc 2 with explanatory stderr.

    Supersedes the earlier behavior where a single save silently produced
    an empty JSON array. The help text says "specify at least twice", so
    honour that contract loudly.
    """
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusdiff",
         "--save", str(FIXTURES / "per_player_a.db"),
         "--out", str(tmp_path / "x.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "at least twice" in result.stderr.lower()


def test_corpusdiff_fails_loud_on_non_sqlite_save(tmp_path: Path) -> None:
    not_a_db = tmp_path / "not.db"
    not_a_db.write_text("this is not a sqlite file")
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusdiff",
         "--save", str(not_a_db),
         "--save", str(FIXTURES / "per_player_b.db"),
         "--out", str(tmp_path / "x.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    combined = result.stderr.lower()
    assert "not a valid sqlite" in combined or "database" in combined
