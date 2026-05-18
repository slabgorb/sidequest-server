"""Read-only DB assembly for the save-forensics page.

Mirrors the module-level ``query_encounter_events(store)`` precedent:
plain functions over an open SQLite connection. Never writes, never
checkpoints (respects the WAL/save-clobber hazard).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    """Strictly read-only: no schema init, no migration, no WAL flip.

    ``SqliteStore.open`` writes on construction (schema + migrations +
    commit + journal_mode=WAL) — forbidden here per the save-clobber hazard.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # NOTE: opening a WAL-mode save read-only still materializes a harmless
    # save.db-shm (SQLite read-side shared-memory index) — NOT a main-db
    # write; list_saves' read-only/byte-identity contract is unaffected.
    conn.row_factory = sqlite3.Row
    return conn


def list_saves(save_dir: Path) -> list[dict]:
    """Enumerate ``<save_dir>/games/<slug>/save.db`` files.

    Broken/meta-less DBs are skipped *loudly* (logged WARNING), never
    silently. Sorted newest-first by save-file mtime.
    """
    games_root = Path(save_dir) / "games"
    out: list[dict] = []
    if not games_root.exists():
        return out
    for slug_dir in sorted(games_root.iterdir()):
        if not slug_dir.is_dir():
            continue
        db_file = slug_dir / "save.db"
        if not db_file.is_file():
            continue
        conn: sqlite3.Connection | None = None
        try:
            conn = _ro_connect(db_file)
            row = conn.execute(
                "SELECT genre_slug, world_slug, created_at, last_played "
                "FROM session_meta WHERE id = 1"
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 — best-effort enumeration
            logger.warning("forensic_query.open_failed slug=%s err=%s", slug_dir.name, exc)
            continue
        finally:
            if conn is not None:
                conn.close()
        if row is None:
            logger.warning("forensic_query.no_meta slug=%s", slug_dir.name)
            continue
        out.append(
            {
                "slug": slug_dir.name,
                "genre": row["genre_slug"],
                "world": row["world_slug"],
                "created_at": row["created_at"],
                "last_played": row["last_played"],
                "last_activity_ts": int(db_file.stat().st_mtime * 1000),
            }
        )
    out.sort(key=lambda r: r["last_activity_ts"], reverse=True)
    return out
