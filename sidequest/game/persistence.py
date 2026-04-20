"""SQLite session persistence — Python port of sidequest_game::persistence.

Port of sidequest_game::persistence (persistence.rs, 581 LOC).

Schema matches the Rust save format exactly (table names, column names,
column types). GameSnapshot is serialized as JSON TEXT in the game_state
table — same as Rust's serde_json::to_string.

NOTE on Rust save compatibility:
  The Rust SqliteStore serializes GameSnapshot using serde_json::to_string
  which produces flattened JSON (CreatureCore fields appear at the Npc/Character
  level due to #[serde(flatten)]). The Python GameSnapshot uses nested core:
  CreatureCore. This means a Rust save will not load directly via
  GameSnapshot.model_validate_json() without a migration step.

  Status: DONE_WITH_CONCERNS — Python round-trips work correctly.
  Loading a Rust save requires a migration shim (flatten → nested transform).
  That shim is deferred and documented in docs/port-notes/game-phase1-slice.md.
  Tests use Python-only round-trips and skip Rust save loading.

ADR-006: One .db file per genre/world/player session.
ADR-023: Auto-save after every turn, atomic writes via SQLite transactions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sidequest.game.session import GameSnapshot, NarrativeEntry


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    genre_slug TEXT NOT NULL,
    world_slug TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_played TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS game_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS narrative_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_narrative_round ON narrative_log(round_number);
CREATE INDEX IF NOT EXISTS idx_narrative_author ON narrative_log(author);
CREATE TABLE IF NOT EXISTS lore_fragments (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    turn_created INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lore_category ON lore_fragments(category);
CREATE TABLE IF NOT EXISTS scenario_archive (
    session_id TEXT PRIMARY KEY,
    scenario_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS scrapbook_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL,
    scene_title TEXT,
    scene_type TEXT,
    location TEXT NOT NULL,
    image_url TEXT,
    narrative_excerpt TEXT NOT NULL,
    world_facts TEXT NOT NULL DEFAULT '[]',
    npcs_present TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scrapbook_turn ON scrapbook_entries(turn_id);
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    """Session metadata from the session_meta table."""

    genre_slug: str
    world_slug: str
    created_at: datetime
    last_played: datetime


@dataclass
class SavedSession:
    """A loaded session: metadata + game state + optional recap."""

    meta: SessionMeta
    snapshot: GameSnapshot
    recap: Optional[str]


# ---------------------------------------------------------------------------
# PersistError
# ---------------------------------------------------------------------------


class PersistError(Exception):
    """Errors from persistence operations.

    Port of sidequest_game::persistence::PersistError.
    """


class NotFoundError(PersistError):
    """Save not found."""


class DatabaseError(PersistError):
    """SQLite database error."""


class SerializationError(PersistError):
    """JSON serialization error."""


# ---------------------------------------------------------------------------
# SqliteStore
# ---------------------------------------------------------------------------


class SqliteStore:
    """SQLite-backed session store. One .db file per save slot.

    Port of sidequest_game::persistence::SqliteStore.

    Uses singleton tables (session_meta, game_state) plus append-only
    narrative_log. Python uses stdlib sqlite3 instead of rusqlite.

    Rust compatibility note: see module docstring.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._init_schema()

    @classmethod
    def open_in_memory(cls) -> "SqliteStore":
        """Open an in-memory store (for testing)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return cls(conn)

    @classmethod
    def open(cls, path: str) -> "SqliteStore":
        """Open a file-backed store."""
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return cls(conn)

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def init_session(self, genre_slug: str, world_slug: str) -> None:
        """Initialize session metadata (genre + world). Call once for new sessions."""
        now = _now_rfc3339()
        self._conn.execute(
            """INSERT OR REPLACE INTO session_meta
               (id, genre_slug, world_slug, created_at, last_played, schema_version)
               VALUES (1, ?, ?, ?, ?, 1)""",
            (genre_slug, world_slug, now, now),
        )
        self._conn.commit()

    def save(self, snapshot: GameSnapshot) -> None:
        """Save the current game state.

        Serializes GameSnapshot to JSON and stores in game_state table.
        Updates last_played in session_meta. Atomic via transaction.
        """
        now = datetime.now(tz=timezone.utc)
        snapshot_copy = snapshot.model_copy(update={"last_saved_at": now})
        state_json = snapshot_copy.model_dump_json()
        now_str = now.isoformat()

        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO game_state (id, snapshot_json, saved_at)
                   VALUES (1, ?, ?)""",
                (state_json, now_str),
            )
            self._conn.execute(
                "UPDATE session_meta SET last_played = ? WHERE id = 1",
                (now_str,),
            )

    def load(self) -> Optional[SavedSession]:
        """Load the saved session, or None if no save exists."""
        row = self._conn.execute(
            "SELECT snapshot_json FROM game_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        snapshot = GameSnapshot.model_validate_json(row[0])
        meta = self._load_meta() or SessionMeta(
            genre_slug=snapshot.genre_slug,
            world_slug=snapshot.world_slug,
            created_at=datetime.now(tz=timezone.utc),
            last_played=datetime.now(tz=timezone.utc),
        )
        entries = self.recent_narrative(3)
        character_names = [ch.core.name for ch in snapshot.characters]
        known_facts = snapshot.characters[0].known_facts if snapshot.characters else []
        recap = _generate_recap(entries, character_names, snapshot.location, known_facts)
        return SavedSession(meta=meta, snapshot=snapshot, recap=recap)

    def append_narrative(self, entry: NarrativeEntry) -> None:
        """Append a narrative entry to the log."""
        import json
        tags_json = json.dumps(entry.tags)
        self._conn.execute(
            """INSERT INTO narrative_log (round_number, author, content, tags)
               VALUES (?, ?, ?, ?)""",
            (entry.round, entry.author, entry.content, tags_json),
        )
        self._conn.commit()

    def recent_narrative(self, limit: int) -> list[NarrativeEntry]:
        """Get the most recent narrative entries, ordered oldest-first."""
        import json
        rows = self._conn.execute(
            """SELECT round_number, author, content, tags
               FROM (SELECT * FROM narrative_log ORDER BY id DESC LIMIT ?)
               ORDER BY id ASC""",
            (limit,),
        ).fetchall()
        entries = []
        for row in rows:
            tags_json = row[3] or "[]"
            try:
                tags = json.loads(tags_json)
            except Exception:
                tags = []
            entries.append(
                NarrativeEntry(
                    timestamp=0,
                    round=row[0],
                    author=row[1],
                    content=row[2],
                    tags=tags,
                )
            )
        return entries

    def generate_recap(self) -> Optional[str]:
        """Generate a 'Previously On...' recap from recent entries."""
        entries = self.recent_narrative(3)
        if not entries:
            return None
        recap = "## Previously On\u2026\n\n"
        for entry in entries:
            content = entry.content
            if len(content) > 200:
                content = content[:200] + "..."
            recap += f"- {content}\n"
        return recap

    def _load_meta(self) -> Optional[SessionMeta]:
        row = self._conn.execute(
            """SELECT genre_slug, world_slug, created_at, last_played
               FROM session_meta WHERE id = 1"""
        ).fetchone()
        if row is None:
            return None
        return SessionMeta(
            genre_slug=row[0],
            world_slug=row[1],
            created_at=_parse_rfc3339(row[2]),
            last_played=_parse_rfc3339(row[3]),
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def db_path_for_session(
    save_dir: Path,
    genre_slug: str,
    world_slug: str,
    player_name: str,
) -> Path:
    """Compute the .db file path for a genre/world/player triple.

    Mirrors PersistenceWorker::db_path in Rust.
    """
    safe = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in player_name
    ).lower() or "default"
    return save_dir / genre_slug / world_slug / safe / "save.db"


def _now_rfc3339() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_rfc3339(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now(tz=timezone.utc)


def _generate_recap(
    entries: list[NarrativeEntry],
    character_names: list[str],
    location: str,
    known_facts: list,
) -> Optional[str]:
    """Generate a 'Previously On...' recap.

    Port of sidequest_game::narrative::generate_recap_with_facts.
    Uses known_facts as primary source, falls back to narration entries.
    """
    if not entries and not known_facts:
        return None

    lines = ["## Previously On\u2026\n"]
    if character_names:
        party = ", ".join(character_names)
        lines.append(f"The party — {party} — had been adventuring.\n")

    if known_facts:
        # Use up to 8 most recent known facts
        for fact in known_facts[-8:]:
            content = getattr(fact, "content", None) or (
                fact.get("content", "") if isinstance(fact, dict) else ""
            )
            if content:
                lines.append(f"- {content}")
    elif entries:
        for entry in entries:
            content = entry.content
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"- {content}")

    if location:
        lines.append(f"\nThe party now finds themselves at {location}.")

    return "\n".join(lines)
