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
        reader.conn.execute(
            "INSERT INTO events (kind, payload_json, created_at) VALUES ('X', '{}', 'now')"
        )


def test_save_reader_does_not_mutate_mtime(tmp_path: Path) -> None:
    copy = tmp_path / "copy.db"
    copy.write_bytes(SINGLE.read_bytes())
    before = copy.stat().st_mtime_ns
    with SaveReader(copy) as reader:
        list(reader.iter_events())
        list(reader.iter_narrative_log())
    after = copy.stat().st_mtime_ns
    assert before == after, "opening readonly must not touch mtime"


def test_save_reader_does_not_touch_wal_sidecars(tmp_path: Path) -> None:
    """Against a WAL-mode save, immutable=1 must prevent touching -wal/-shm."""
    import sqlite3 as _s

    wal_db = tmp_path / "wal.db"
    # Build a tiny WAL-mode DB with one session_meta and one narrative_log row.
    conn = _s.connect(wal_db)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE session_meta (id INTEGER PRIMARY KEY CHECK (id=1),
            genre_slug TEXT NOT NULL, world_slug TEXT NOT NULL,
            created_at TEXT NOT NULL, last_played TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1);
        INSERT INTO session_meta VALUES (1,'g','w','2026-04-24T00:00:00Z','2026-04-24T00:00:00Z',1);
        CREATE TABLE narrative_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_number INTEGER NOT NULL, author TEXT NOT NULL,
            content TEXT NOT NULL, tags TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO narrative_log (round_number, author, content) VALUES (1, 'narrator', 'hi');
        CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    conn.close()

    wal = wal_db.with_suffix(wal_db.suffix + "-wal")
    shm = wal_db.with_suffix(wal_db.suffix + "-shm")
    # WAL sidecars may or may not exist depending on whether there are uncommitted
    # pages; capture mtimes only if they do.
    before = {p: p.stat().st_mtime_ns for p in (wal_db, wal, shm) if p.exists()}

    with SaveReader(wal_db) as reader:
        list(reader.iter_narrative_log())

    after = {p: p.stat().st_mtime_ns for p in before}
    assert before == after, f"SaveReader touched {[p for p in before if before[p] != after[p]]}"
