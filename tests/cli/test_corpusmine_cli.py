from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_corpusmine_writes_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "mined.jsonl"
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusmine",
         "--save", str(FIXTURES / "single_session.db"),
         "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert out.exists()
    lines = out.read_text().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert first["schema_version"] == 1
    assert "wrote" in result.stdout.lower()


def test_corpusmine_fails_loud_on_missing_save(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusmine",
         "--save", str(tmp_path / "nope.db"),
         "--out", str(tmp_path / "x.jsonl")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "no such file" in result.stderr.lower()


def test_corpusmine_requires_save_and_out_flags() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.corpusmine"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
