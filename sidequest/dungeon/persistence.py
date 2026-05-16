"""Beneath Sünden Plan 5 — dungeon persistence layer.

Persists the contiguous region graph, frontier, mutation overlay, and
complication ledger into the existing per-session SQLite save DB. The
store operates on a CALLER-SUPPLIED connection (never opens its own) so
Plan 7's materializer can wrap game-save + dungeon-save in one
transaction (spec §7.5). No materializer/session caller exists yet —
honest deferral, Plan 2-4 precedent (verified by the wiring-contract
test, not stubbed).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sidequest.game.persistence import (
    DatabaseError,
    NotFoundError,
    PersistError,
    SerializationError,
)

__all__ = [
    "DungeonStore",
    "PersistError",
    "NotFoundError",
    "DatabaseError",
    "SerializationError",
]

# Bumped only when a frozen on-disk region's bytes would change. Plan 5
# stamps it per region at commit; frozen regions are never rewritten
# (spec §7). The freeze test bumps this constant to prove immutability.
GENERATOR_VERSION = "plan5.v1"

DUNGEON_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dungeon_map (
    region_id TEXT PRIMARY KEY,
    expansion_id INTEGER NOT NULL,
    depth_score REAL,
    generator_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    mask BLOB,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dungeon_map_expansion ON dungeon_map(expansion_id);
CREATE INDEX IF NOT EXISTS idx_dungeon_map_depth ON dungeon_map(depth_score);

CREATE TABLE IF NOT EXISTS dungeon_edge (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    expansion_id INTEGER NOT NULL,
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    kind TEXT NOT NULL,
    hidden INTEGER NOT NULL,
    shortcut INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dungeon_edge_a ON dungeon_edge(a);
CREATE INDEX IF NOT EXISTS idx_dungeon_edge_b ON dungeon_edge(b);
CREATE INDEX IF NOT EXISTS idx_dungeon_edge_expansion ON dungeon_edge(expansion_id);

CREATE TABLE IF NOT EXISTS dungeon_frontier (
    frontier_edge_id TEXT PRIMARY KEY,
    from_region_id TEXT NOT NULL,
    heading TEXT NOT NULL,
    spawn_depth_score REAL NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dungeon_frontier_from ON dungeon_frontier(from_region_id);

CREATE TABLE IF NOT EXISTS dungeon_mutation_overlay (
    mutation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dungeon_mutation_region ON dungeon_mutation_overlay(region_id);

CREATE TABLE IF NOT EXISTS dungeon_complication_ledger (
    thread_id TEXT PRIMARY KEY,
    origin_region_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at_depth_score REAL NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dungeon_ledger_status ON dungeon_complication_ledger(status);
CREATE INDEX IF NOT EXISTS idx_dungeon_ledger_origin ON dungeon_complication_ledger(origin_region_id);
"""


class DungeonStore:
    """Dungeon persistence over a caller-supplied save-DB connection.

    Does NOT open or own the connection (Plan 7 passes the live session
    connection so its commit wraps game-save + dungeon-save in one
    transaction, spec §7.5). Does NOT autocommit — the caller owns the
    transaction boundary.
    """

    def __init__(self, conn: sqlite3.Connection | Path) -> None:
        if isinstance(conn, Path):
            raise PersistError(
                "DungeonStore requires a caller-supplied sqlite3.Connection "
                "(it never opens its own DB — Plan 7 owns the connection so "
                "game-save + dungeon-save share one transaction, spec §7.5)"
            )
        self._conn = conn

    def ensure_schema(self) -> None:
        """Idempotent additive schema creation. No migration framework —
        additive CREATE TABLE IF NOT EXISTS only (spec Decision 5).

        Must be called before any transaction is opened on the
        connection — sqlite3.executescript() issues an implicit COMMIT
        on any pending transaction. Plan 7 must call this at
        session-open time, not within a BEGIN block (spec §7.5
        one-transaction contract).
        """
        try:
            self._conn.executescript(DUNGEON_SCHEMA_SQL)
        except sqlite3.Error as exc:  # fail loud — no silent fallback
            raise DatabaseError(f"dungeon schema creation failed: {exc}") from exc
