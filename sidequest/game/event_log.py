"""Monotonic event log for a single game slug.

Every narrator-originated mutation (NARRATION, STATE_UPDATE, COMBAT_EVENT, etc.)
is appended here before fan-out. Peers catch up on reconnect via read_since.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

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

    @property
    def store(self) -> SqliteStore:
        """Store accessor for transaction-scoped batch writes."""
        return self._store

    def append(self, *, kind: str, payload_json: str) -> EventRow:
        """Append an event, committing its own transaction.

        Used by callers outside the fan-out hot-path. Fan-out should use
        ``append_in_transaction`` so the event insert + cache writes share
        a single transaction — see ProjectionFilter-Rules spec (C2).
        """
        with self._store._conn:
            return self.append_in_transaction(
                kind=kind, payload_json=payload_json, conn=self._store._conn
            )

    def append_in_transaction(
        self,
        *,
        kind: str,
        payload_json: str,
        conn: sqlite3.Connection,
    ) -> EventRow:
        """Append an event using a caller-managed connection/transaction.

        Does NOT commit. The caller owns the transaction (typically via
        ``with store._conn:``) so that the event row and its associated
        ProjectionCache rows can be persisted atomically.
        """
        now = datetime.now(tz=UTC).isoformat()
        cur = conn.execute(
            "INSERT INTO events (kind, payload_json, created_at) VALUES (?, ?, ?)",
            (kind, payload_json, now),
        )
        seq = cur.lastrowid
        assert seq is not None
        return EventRow(seq=seq, kind=kind, payload_json=payload_json, created_at=now)

    def read_since(self, *, since_seq: int) -> list[EventRow]:
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
