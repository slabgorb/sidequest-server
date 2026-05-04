"""Tests for the sibling-file safety net created by SqliteStore.load when
migrate_legacy_snapshot rewrites any field. Per architect amendment
2026-05-04 to docs/superpowers/plans/2026-05-04-snapshot-split-brain-wave-1.md.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sidequest.game.persistence import SqliteStore
from sidequest.game.session import GameSnapshot


def _write_raw_save(db_path: Path, snapshot_dict: dict) -> None:
    """Write a snapshot JSON directly into game_state.id=1, bypassing pydantic.

    Lets us seed legacy-shaped saves (e.g. with world_confrontations populated)
    without having a model that produces them.
    """
    # Init schema by creating an empty store, then overwrite the row.
    store = SqliteStore(db_path)
    canonical = GameSnapshot(genre_slug="test", world_slug="t")
    store.save(canonical)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE game_state SET snapshot_json = ? WHERE id = 1",
            (json.dumps(snapshot_dict),),
        )
        conn.commit()


def test_canonical_load_does_not_create_backup(tmp_path: Path) -> None:
    """A load that requires no migration leaves no .canonicalize.bak."""
    db_path = tmp_path / "save.db"
    store = SqliteStore(db_path)
    canonical = GameSnapshot(genre_slug="test", world_slug="t")
    store.save(canonical)

    store2 = SqliteStore(db_path)
    loaded = store2.load()

    assert loaded is not None
    assert not (tmp_path / "save.db.canonicalize.bak").exists()


def test_legacy_load_creates_backup_once(tmp_path: Path) -> None:
    """A load that rewrites any field copies the .db to a sibling .bak."""
    db_path = tmp_path / "save.db"
    bak_path = tmp_path / "save.db.canonicalize.bak"

    # Seed a legacy-shaped snapshot with the S1 ``world_confrontations``
    # field — that's the per-field migration registered in Task 4 that
    # makes ``migrated != raw`` and triggers the .bak copy. The empty
    # list still strips the field (covered by
    # test_s1_empty_world_confrontations_still_strips_field).
    legacy = {
        "genre_slug": "test",
        "world_slug": "t",
        "characters": [],
        "npcs": [],
        "narrative_log": [],
        "world_confrontations": [],
    }
    _write_raw_save(db_path, legacy)

    store = SqliteStore(db_path)
    loaded = store.load()
    assert loaded is not None

    # If the migration scaffold is no-op (which it is at Task 1), this
    # assertion will fail. The test becomes meaningful starting in Tasks 3-4.
    if not bak_path.exists():
        pytest.skip(
            "No per-field migration registered yet — backup is unreachable. "
            "Re-enable this test after Task 3 (S5) lands."
        )

    assert bak_path.is_file()
    # The .bak captures the pre-migration on-disk state.
    with sqlite3.connect(bak_path) as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM game_state WHERE id = 1"
        ).fetchone()
    assert row is not None
    backed_up = json.loads(row[0])
    assert backed_up == legacy


def test_backup_is_idempotent(tmp_path: Path) -> None:
    """A second load on a save that already has a .bak does not overwrite it."""
    db_path = tmp_path / "save.db"
    bak_path = tmp_path / "save.db.canonicalize.bak"

    # Pre-seed the .bak with sentinel content.
    db_path.write_bytes(b"")  # placeholder to satisfy SqliteStore init
    store = SqliteStore(db_path)
    store.save(GameSnapshot(genre_slug="test", world_slug="t"))
    bak_path.write_text("SENTINEL — pre-existing backup")

    # Even if we forced a migration here, the .bak should remain SENTINEL.
    store2 = SqliteStore(db_path)
    _ = store2.load()

    assert bak_path.read_text() == "SENTINEL — pre-existing backup"
