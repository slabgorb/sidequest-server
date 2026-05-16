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

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionEdge,
    RegionGraph,
    RegionNode,
)
from sidequest.game.persistence import (
    DatabaseError,
    NotFoundError,
    PersistError,
    SerializationError,
)

__all__ = [
    "DungeonStore",
    "FrontierEdge",
    "PersistError",
    "NotFoundError",
    "DatabaseError",
    "SerializationError",
]

# Bumped only when a frozen on-disk region's bytes would change. Plan 5
# stamps it per region at commit; frozen regions are never rewritten
# (spec §7). The freeze test bumps this constant to prove immutability.
GENERATOR_VERSION = "plan5.v1"


@dataclass(frozen=True)
class FrontierEdge:
    """An unexpanded frontier edge — where, and at what depth, an
    expansion would spawn. Plan 7's materializer is the producer."""

    frontier_edge_id: str
    from_region_id: str
    heading: str
    spawn_depth_score: float

    def to_dict(self) -> dict:
        return {
            "frontier_edge_id": self.frontier_edge_id,
            "from_region_id": self.from_region_id,
            "heading": self.heading,
            "spawn_depth_score": self.spawn_depth_score,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FrontierEdge:
        return cls(
            frontier_edge_id=d["frontier_edge_id"],
            from_region_id=d["from_region_id"],
            heading=d["heading"],
            spawn_depth_score=d["spawn_depth_score"],
        )


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

    def commit_expansion(
        self,
        expansion: Expansion,
        graph: RegionGraph,
        *,
        generator_version: str = GENERATOR_VERSION,
    ) -> None:
        """Persist one expansion's regions + edges WITHIN the caller's
        transaction (no autocommit — Plan 7 owns the txn boundary,
        spec §7.5). Regions are read from `graph` (depth-scored); edge
        ownership is taken from `expansion`.
        """
        try:
            for node in expansion.new_nodes:
                live = graph.nodes.get(node.id)
                if live is None:
                    raise NotFoundError(
                        f"expansion region {node.id!r} is not in the graph "
                        f"(commit must run after attach_expansion)"
                    )
                self._conn.execute(
                    "INSERT INTO dungeon_map "
                    "(region_id, expansion_id, depth_score, generator_version, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        live.id,
                        live.expansion_id,
                        live.depth_score,
                        generator_version,
                        json.dumps(live.to_dict()),
                    ),
                )
            for edge in expansion.new_edges:
                self._conn.execute(
                    "INSERT INTO dungeon_edge "
                    "(expansion_id, a, b, kind, hidden, shortcut, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        expansion.expansion_id,
                        edge.a,
                        edge.b,
                        edge.kind,
                        int(edge.hidden),
                        int(edge.shortcut),
                        json.dumps(edge.to_dict()),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # A region_id already committed = a re-commit of a frozen
            # expansion. Fail loud (spec §7: frozen regions never rewritten).
            raise PersistError(
                f"dungeon expansion {expansion.expansion_id} re-commit "
                f"violates the freeze contract: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise DatabaseError(f"commit_expansion failed: {exc}") from exc

    def load_map(self, *, entrance_id: str) -> RegionGraph:
        """Rebuild the full RegionGraph from dungeon_map + dungeon_edge.
        Nodes first (RegionGraph.add_edge validates endpoints loudly)."""
        try:
            node_rows = self._conn.execute(
                "SELECT payload FROM dungeon_map"
            ).fetchall()
            edge_rows = self._conn.execute(
                "SELECT payload FROM dungeon_edge ORDER BY edge_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"load_map query failed: {exc}") from exc

        g = RegionGraph(entrance_id=entrance_id)
        try:
            for r in node_rows:
                g.add_node(RegionNode.from_dict(json.loads(r["payload"])))
            for r in edge_rows:
                g.add_edge(RegionEdge.from_dict(json.loads(r["payload"])))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise SerializationError(f"corrupt dungeon payload: {exc}") from exc
        return g

    def put_frontier(self, fe: FrontierEdge) -> None:
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO dungeon_frontier "
                "(frontier_edge_id, from_region_id, heading, spawn_depth_score, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    fe.frontier_edge_id,
                    fe.from_region_id,
                    fe.heading,
                    fe.spawn_depth_score,
                    json.dumps(fe.to_dict()),
                ),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"put_frontier failed: {exc}") from exc

    def load_frontier(self) -> list[FrontierEdge]:
        try:
            rows = self._conn.execute(
                "SELECT payload FROM dungeon_frontier ORDER BY frontier_edge_id"
            ).fetchall()
            return [FrontierEdge.from_dict(json.loads(r["payload"])) for r in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(f"load_frontier failed: {exc}") from exc
        except (json.JSONDecodeError, KeyError) as exc:
            raise SerializationError(f"corrupt frontier payload: {exc}") from exc
