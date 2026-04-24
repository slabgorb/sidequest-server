from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sidequest.corpus.save_reader import SaveReader

FIXTURES = Path(__file__).parents[1] / "cli" / "fixtures"
SINGLE = FIXTURES / "single_session.db"


def test_save_reader_opens_readonly() -> None:
    with SaveReader(SINGLE) as reader:
        rows = list(reader.iter_events())
        assert len(rows) == 3


def test_save_reader_refuses_writes() -> None:
    with SaveReader(SINGLE) as reader, pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.conn.execute("INSERT INTO events (kind, payload_json, created_at) VALUES ('X', '{}', 'now')")


def test_save_reader_does_not_mutate_mtime(tmp_path: Path) -> None:
    copy = tmp_path / "copy.db"
    copy.write_bytes(SINGLE.read_bytes())
    before = copy.stat().st_mtime_ns
    with SaveReader(copy) as reader:
        list(reader.iter_events())
        list(reader.iter_narrative_log())
    after = copy.stat().st_mtime_ns
    assert before == after, "opening readonly must not touch mtime"
