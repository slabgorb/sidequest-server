from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


@dataclass(frozen=True)
class EventRow:
    seq: int
    kind: str
    payload_json: str
    created_at: str


@dataclass(frozen=True)
class NarrativeRow:
    id: int
    round_number: int
    author: str
    content: str
    tags: str | None
    created_at: str


class SaveReader:
    """Open a save.db strictly read-only. Never writes, never updates mtime."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> SaveReader:
        # immutable=1 tells SQLite the file won't change, skipping lock acquisition
        # and avoiding any touch of -wal/-shm sidecars. Necessary for the
        # "does not mutate mtime" contract when mining live WAL-mode saves.
        uri = f"file:{self._path}?mode=ro&immutable=1"
        self._conn = sqlite3.connect(uri, uri=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SaveReader used outside of `with` block")
        return self._conn

    def iter_events(self) -> Iterator[EventRow]:
        cur = self.conn.execute(
            "SELECT seq, kind, payload_json, created_at FROM events ORDER BY seq ASC"
        )
        for row in cur:
            yield EventRow(*row)

    def iter_narrative_log(self) -> Iterator[NarrativeRow]:
        cur = self.conn.execute(
            "SELECT id, round_number, author, content, tags, created_at "
            "FROM narrative_log ORDER BY round_number ASC, id ASC"
        )
        for row in cur:
            yield NarrativeRow(*row)
