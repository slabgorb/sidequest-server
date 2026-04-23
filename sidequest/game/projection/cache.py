"""Per-player projection decision cache.

Backed by the same SQLite DB as EventLog. Written at fan-out time; read
at reconnect. The (event_seq, player_id) primary key means a re-fan of
the same event to the same player is idempotent (last write wins).
"""
from __future__ import annotations

from dataclasses import dataclass

from sidequest.game.persistence import SqliteStore
from sidequest.game.projection_filter import FilterDecision
from sidequest.telemetry.spans import projection_cache_fill_span


@dataclass(frozen=True)
class CachedDecision:
    event_seq: int
    include: bool
    payload_json: str | None


class ProjectionCache:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def write(
        self,
        *,
        event_seq: int,
        player_id: str,
        decision: FilterDecision,
    ) -> None:
        with projection_cache_fill_span(event_seq=event_seq, player_id=player_id):
            payload = decision.payload_json if decision.include else None
            with self._store._conn:
                self._store._conn.execute(
                    """
                    INSERT INTO projection_cache (event_seq, player_id, include, payload_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_seq, player_id) DO UPDATE SET
                        include = excluded.include,
                        payload_json = excluded.payload_json
                    """,
                    (event_seq, player_id, 1 if decision.include else 0, payload),
                )

    def read_since(self, *, player_id: str, since_seq: int) -> list[CachedDecision]:
        with self._store._conn:
            rows = self._store._conn.execute(
                """
                SELECT event_seq, include, payload_json
                FROM projection_cache
                WHERE player_id = ? AND event_seq > ?
                ORDER BY event_seq ASC
                """,
                (player_id, since_seq),
            ).fetchall()
        return [
            CachedDecision(event_seq=r[0], include=bool(r[1]), payload_json=r[2])
            for r in rows
        ]
