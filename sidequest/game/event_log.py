"""Monotonic event log for a single game slug.

Every narrator-originated mutation (NARRATION, STATE_UPDATE, COMBAT_EVENT, etc.)
is appended here before fan-out. Peers catch up on reconnect via read_since.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from sidequest.game.persistence import SqliteStore


@dataclass
class EventRow:
    seq: int
    kind: str
    payload_json: str
    created_at: str


class EventLog:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def append(self, *, kind: str, payload_json: str) -> EventRow:
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._store._conn:
            cur = self._store._conn.execute(
                "INSERT INTO events (kind, payload_json, created_at) VALUES (?, ?, ?)",
                (kind, payload_json, now),
            )
            seq = cur.lastrowid
        assert seq is not None
        return EventRow(seq=seq, kind=kind, payload_json=payload_json, created_at=now)

    def read_since(self, *, since_seq: int) -> List[EventRow]:
        with self._store._conn:
            rows = self._store._conn.execute(
                "SELECT seq, kind, payload_json, created_at FROM events WHERE seq > ? ORDER BY seq ASC",
                (since_seq,),
            ).fetchall()
        return [EventRow(seq=r[0], kind=r[1], payload_json=r[2], created_at=r[3]) for r in rows]

    def latest_seq(self) -> int:
        with self._store._conn:
            row = self._store._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return int(row[0])
