from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidequest.corpus.schema import MineProvenance, TrainingPair
from sidequest.corpus.writer import write_pairs


def _pair(i: int) -> TrainingPair:
    return TrainingPair(
        schema_version=1,
        genre="test",
        world="test",
        round_number=i,
        input_text=f"in{i}",
        output_text=f"out{i}",
        provenance=MineProvenance(source_save="x.db", event_seq=None),
    )


def test_write_pairs_emits_one_json_object_per_line(tmp_path: Path) -> None:
    out = tmp_path / "corpus.jsonl"
    write_pairs(out, [_pair(1), _pair(2)])
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["round_number"] == 1
    assert json.loads(lines[1])["round_number"] == 2


def test_write_pairs_overwrites_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "corpus.jsonl"
    out.write_text("stale\n")
    write_pairs(out, [_pair(1)])
    content = out.read_text()
    assert "stale" not in content


def test_write_pairs_leaves_no_tmp_file(tmp_path: Path) -> None:
    out = tmp_path / "corpus.jsonl"
    write_pairs(out, [_pair(1)])
    assert not (tmp_path / "corpus.jsonl.tmp").exists()


def test_write_pairs_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "dir" / "corpus.jsonl"
    write_pairs(out, [_pair(1)])
    assert out.exists()


def test_write_pairs_refuses_db_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=".db path"):
        write_pairs(tmp_path / "save.db", [_pair(1)])


def test_write_pairs_refuses_path_under_sidequest_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard against --out ~/.sidequest/saves/... clobbering a real save."""
    from sidequest.corpus import writer as writer_mod

    fake_home = tmp_path / "home"
    fake_saves = fake_home / ".sidequest" / "saves"
    fake_saves.mkdir(parents=True)
    monkeypatch.setattr(writer_mod, "_SAVE_ROOT", fake_saves)
    with pytest.raises(ValueError, match="under"):
        write_pairs(fake_saves / "mine.jsonl", [_pair(1)])


def test_write_pairs_cleans_up_tmp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If replace fails, the .tmp file must not be left behind."""
    out = tmp_path / "corpus.jsonl"

    def boom(self: Path, target: Path) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        write_pairs(out, [_pair(1)])
    # No .tmp.* files left behind
    assert list(tmp_path.glob("corpus.jsonl.tmp*")) == []
