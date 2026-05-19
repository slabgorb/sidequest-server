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
from collections.abc import Mapping
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
from sidequest.telemetry.spans.dungeon_persist import (
    dungeon_persist_commit_span,
    ledger_add_span,
    ledger_resolve_span,
    mask_load_span,
    mask_write_span,
)

__all__ = [
    "ComplicationThread",
    "DungeonMutation",
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


@dataclass(frozen=True)
class DungeonMutation:
    """One append-only mutation fact (sprung trap, looted room,
    collapse, resolved set-piece). Never updated or deleted; load
    replays in mutation_id order over the base map."""

    region_id: str
    kind: str
    payload: dict

    def to_dict(self) -> dict:
        return {"region_id": self.region_id, "kind": self.kind, "payload": self.payload}

    @classmethod
    def from_dict(cls, d: dict) -> DungeonMutation:
        return cls(region_id=d["region_id"], kind=d["kind"], payload=d["payload"])


@dataclass(frozen=True)
class ComplicationThread:
    """A started-but-unresolved trope/quest thread (spec §7.1 — the
    spine). Starts at attach (Plan 6/7 produce it), persists until
    player-resolved. Plan 5 owns storage + status transitions only."""

    thread_id: str
    origin_region_id: str
    kind: str
    status: str
    started_at_depth_score: float
    payload: dict

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "origin_region_id": self.origin_region_id,
            "kind": self.kind,
            "status": self.status,
            "started_at_depth_score": self.started_at_depth_score,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ComplicationThread:
        return cls(
            thread_id=d["thread_id"],
            origin_region_id=d["origin_region_id"],
            kind=d["kind"],
            status=d["status"],
            started_at_depth_score=d["started_at_depth_score"],
            payload=d["payload"],
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

CREATE TABLE IF NOT EXISTS dungeon_meta (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    campaign_seed INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
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

    def get_campaign_seed(self) -> int | None:
        """The persisted campaign seed, or ``None`` on a fresh save.

        Save-is-truth: the seed is frozen at bootstrap and read back
        verbatim every reopen (Plan 7 session-integration spec §5). Fails
        loud on a real sqlite error (No Silent Fallbacks).
        """
        try:
            row = self._conn.execute(
                "SELECT campaign_seed FROM dungeon_meta WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"dungeon_meta read failed: {exc}") from exc
        return None if row is None else int(row["campaign_seed"])

    def set_campaign_seed(self, seed: int) -> None:
        """Persist the campaign seed exactly once (write-once).

        A second write is a contract violation, not an upsert — the seed
        is frozen with the dungeon (save-is-truth). Does NOT autocommit:
        the caller owns the transaction boundary (spec §7.5).
        """
        if self.get_campaign_seed() is not None:
            raise PersistError(
                "campaign_seed already set — it is write-once "
                "(save-is-truth); refusing to overwrite a frozen seed"
            )
        try:
            self._conn.execute(
                "INSERT INTO dungeon_meta (id, campaign_seed) VALUES (1, ?)",
                (seed,),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"dungeon_meta write failed: {exc}") from exc

    def commit_expansion(
        self,
        expansion: Expansion,
        graph: RegionGraph,
        *,
        generator_version: str = GENERATOR_VERSION,
        masks: Mapping[str, dict] | None = None,
    ) -> None:
        """Persist one expansion's regions + edges WITHIN the caller's
        transaction (no autocommit — Plan 7 owns the txn boundary,
        spec §7.5). Regions are read from `graph` (depth-scored); edge
        ownership is taken from `expansion`.

        Story 52-3 adds the optional ``masks`` parameter: a per-region
        map ``{region_id: mask_dict}``. Supplied masks are JSON-encoded
        and written to the ``dungeon_map.mask`` BLOB column (ADR-096
        "the mask is the truth"). Regions absent from the map persist
        as NULL — never a silent default mask. ``masks=None`` (the
        default) leaves every row's BLOB NULL and emits NO write span
        (the spec §6 Illusionism guard).
        """
        with dungeon_persist_commit_span(
            expansion_id=expansion.expansion_id,
            regions=len(expansion.new_nodes),
            edges=len(expansion.new_edges),
            generator_version=generator_version,
        ):
            try:
                for node in expansion.new_nodes:
                    live = graph.nodes.get(node.id)
                    if live is None:
                        raise NotFoundError(
                            f"expansion region {node.id!r} is not in the graph "
                            f"(commit must run after attach_expansion)"
                        )
                    mask_blob: bytes | None = None
                    if masks is not None and live.id in masks:
                        try:
                            mask_blob = json.dumps(masks[live.id], sort_keys=True).encode("utf-8")
                        except (TypeError, ValueError) as exc:
                            # Fail loud — never silently substitute NULL or
                            # stringify a non-serialisable mask payload.
                            raise PersistError(
                                f"mask for region {live.id!r} is not JSON-serialisable: {exc}"
                            ) from exc
                    self._conn.execute(
                        "INSERT INTO dungeon_map "
                        "(region_id, expansion_id, depth_score, generator_version, "
                        " payload, mask) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            live.id,
                            live.expansion_id,
                            live.depth_score,
                            generator_version,
                            json.dumps(live.to_dict()),
                            mask_blob,
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

        # Mask write span fires ONLY when masks were supplied — emitting
        # it for masks=None would be the Illusionism the GM panel exists
        # to catch (spec §6). The span is nested OUTSIDE the commit span
        # because it summarises this commit call's mask-write count.
        if masks is not None:
            with mask_write_span(mask_rows=len(masks)):
                pass

    def load_map(self, *, entrance_id: str) -> RegionGraph:
        """Rebuild the full RegionGraph from dungeon_map + dungeon_edge.
        Nodes first (RegionGraph.add_edge validates endpoints loudly)."""
        try:
            node_rows = self._conn.execute("SELECT payload FROM dungeon_map").fetchall()
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

    def load_masks(self) -> dict[str, dict]:
        """Return persisted region masks as ``{region_id: mask_dict}``.

        Rows whose ``mask BLOB`` is NULL are OMITTED from the result —
        callers distinguish "no mask known" from "mask is {}" by the
        absence/presence of the key. A fresh save returns ``{}``; a
        corrupted BLOB raises ``SerializationError`` (No Silent
        Fallbacks). The corresponding ``dungeon.persist.mask_load`` span
        always fires (carries ``mask_rows`` so the GM panel can confirm
        the load path engaged).
        """
        try:
            rows = self._conn.execute(
                "SELECT region_id, mask FROM dungeon_map WHERE mask IS NOT NULL ORDER BY region_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"load_masks query failed: {exc}") from exc

        masks: dict[str, dict] = {}
        try:
            for r in rows:
                masks[r["region_id"]] = json.loads(r["mask"].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            raise SerializationError(f"corrupt dungeon mask BLOB: {exc}") from exc

        with mask_load_span(mask_rows=len(masks)):
            pass
        return masks

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

    def record_mutation(self, region_id: str, kind: str, payload: dict) -> None:
        try:
            self._conn.execute(
                "INSERT INTO dungeon_mutation_overlay (region_id, kind, payload) VALUES (?, ?, ?)",
                (region_id, kind, json.dumps(payload)),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"record_mutation failed: {exc}") from exc

    def load_mutations(self) -> list[DungeonMutation]:
        try:
            rows = self._conn.execute(
                "SELECT region_id, kind, payload FROM dungeon_mutation_overlay ORDER BY mutation_id"
            ).fetchall()
            return [
                DungeonMutation(
                    region_id=r["region_id"],
                    kind=r["kind"],
                    payload=json.loads(r["payload"]),
                )
                for r in rows
            ]
        except sqlite3.Error as exc:
            raise DatabaseError(f"load_mutations failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SerializationError(f"corrupt mutation payload: {exc}") from exc

    def open_thread(self, thread: ComplicationThread) -> None:
        with ledger_add_span(
            thread_id=thread.thread_id,
            kind=thread.kind,
            origin_region_id=thread.origin_region_id,
        ):
            try:
                self._conn.execute(
                    "INSERT INTO dungeon_complication_ledger "
                    "(thread_id, origin_region_id, kind, status, "
                    " started_at_depth_score, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        thread.thread_id,
                        thread.origin_region_id,
                        thread.kind,
                        thread.status,
                        thread.started_at_depth_score,
                        json.dumps(thread.payload),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistError(f"thread {thread.thread_id!r} already open: {exc}") from exc
            except sqlite3.Error as exc:
                raise DatabaseError(f"open_thread failed: {exc}") from exc

    def get_thread(self, thread_id: str) -> ComplicationThread:
        row = self._conn.execute(
            "SELECT thread_id, origin_region_id, kind, status, "
            "started_at_depth_score, payload FROM dungeon_complication_ledger "
            "WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"complication thread {thread_id!r} not found")
        return ComplicationThread(
            thread_id=row["thread_id"],
            origin_region_id=row["origin_region_id"],
            kind=row["kind"],
            status=row["status"],
            started_at_depth_score=row["started_at_depth_score"],
            payload=json.loads(row["payload"]),
        )

    def resolve_thread(self, thread_id: str) -> None:
        with ledger_resolve_span(thread_id=thread_id):
            cur = self._conn.execute(
                "UPDATE dungeon_complication_ledger "
                "SET status = 'resolved', resolved_at = datetime('now') "
                "WHERE thread_id = ?",
                (thread_id,),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"cannot resolve unknown complication thread {thread_id!r}")

    def open_threads(self) -> list[ComplicationThread]:
        rows = self._conn.execute(
            "SELECT thread_id, origin_region_id, kind, status, "
            "started_at_depth_score, payload FROM dungeon_complication_ledger "
            "WHERE status = 'open' ORDER BY thread_id"
        ).fetchall()
        return [
            ComplicationThread(
                thread_id=r["thread_id"],
                origin_region_id=r["origin_region_id"],
                kind=r["kind"],
                status=r["status"],
                started_at_depth_score=r["started_at_depth_score"],
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]
