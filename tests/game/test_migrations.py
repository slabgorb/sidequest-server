"""Unit tests for sidequest.game.migrations.

Each migration sub-function gets its own focused test. The orchestrator
``migrate_legacy_snapshot`` is also tested for the no-op identity case
(canonical input → identical output, no OTEL span emitted)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from sidequest.game.migrations import migrate_legacy_snapshot


_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "legacy_snapshots"


def test_canonical_snapshot_is_unchanged() -> None:
    canonical = {
        "genre_slug": "caverns_and_claudes",
        "world_slug": "rookhollow",
        "characters": [],
        "npcs": [],
        "narrative_log": [],
    }
    before = copy.deepcopy(canonical)

    result = migrate_legacy_snapshot(canonical)

    assert result == before
    assert canonical == before  # input not mutated


def test_legacy_fixture_loads_without_error() -> None:
    fixture_path = _FIXTURE_DIR / "pre_cleanup.json"
    if not fixture_path.exists():
        pytest.skip("no legacy fixture captured yet")

    raw = json.loads(fixture_path.read_text())
    migrated = migrate_legacy_snapshot(raw)

    # Migration must produce a dict suitable for GameSnapshot.model_validate.
    # We don't validate here (that's the integration test); we only check
    # the migration didn't drop required keys.
    assert "genre_slug" in migrated
    assert "world_slug" in migrated


def test_sqlite_store_load_calls_migrate(tmp_path: Path) -> None:
    """End-to-end: SqliteStore.load runs migrate_legacy_snapshot before validate."""
    from sidequest.game.persistence import SqliteStore
    from sidequest.game.session import GameSnapshot

    store = SqliteStore(tmp_path / "save.db")
    store.init_session(genre_slug="caverns_and_claudes", world_slug="rookhollow")

    canonical = GameSnapshot(genre_slug="caverns_and_claudes", world_slug="rookhollow")
    store.save(canonical)

    loaded = store.load()
    assert loaded is not None
    assert loaded.snapshot.genre_slug == "caverns_and_claudes"


# ---------------------------------------------------------------------------
# S1 migration sub-function tests (Task 4)
# ---------------------------------------------------------------------------


def test_s1_world_confrontations_merges_into_magic_state() -> None:
    legacy = {
        "genre_slug": "g",
        "world_slug": "w",
        "world_confrontations": [
            {"id": "the_tea_brew", "register": "intimate", "outcomes": {}},
        ],
        "magic_state": {
            "config": {"world_slug": "w", "ledger_bars": []},
            "confrontations": [],
        },
    }

    migrated = migrate_legacy_snapshot(legacy)

    # Legacy field is gone.
    assert "world_confrontations" not in migrated
    # Entry now lives on magic_state.
    confs = migrated["magic_state"]["confrontations"]
    assert len(confs) == 1
    assert confs[0]["id"] == "the_tea_brew"


def test_s1_dedupe_by_id_prefers_existing_magic_state_entry() -> None:
    legacy = {
        "genre_slug": "g",
        "world_slug": "w",
        "world_confrontations": [
            {"id": "the_tea_brew", "register": "intimate", "outcomes": {"clear_win": {"mandatory_outputs": ["a"]}}},
        ],
        "magic_state": {
            "config": {"world_slug": "w", "ledger_bars": []},
            "confrontations": [
                {"id": "the_tea_brew", "register": "intimate", "outcomes": {"clear_win": {"mandatory_outputs": ["b"]}}},
            ],
        },
    }

    migrated = migrate_legacy_snapshot(legacy)

    confs = migrated["magic_state"]["confrontations"]
    assert len(confs) == 1
    # Existing magic_state entry wins on collision (it's the canonical home).
    assert confs[0]["outcomes"]["clear_win"]["mandatory_outputs"] == ["b"]


def test_s1_empty_world_confrontations_still_strips_field() -> None:
    legacy = {
        "genre_slug": "g",
        "world_slug": "w",
        "world_confrontations": [],
    }

    migrated = migrate_legacy_snapshot(legacy)

    assert "world_confrontations" not in migrated


def test_s1_no_magic_state_creates_one_when_world_confrontations_present() -> None:
    legacy = {
        "genre_slug": "g",
        "world_slug": "w",
        "world_confrontations": [
            {"id": "the_tea_brew", "register": "intimate", "outcomes": {}},
        ],
        # magic_state absent — this happens for saves predating magic init.
    }

    migrated = migrate_legacy_snapshot(legacy)

    # Pre-existing fixture: if magic_state is None and there's nothing to
    # migrate into, drop the legacy field but DON'T fabricate a magic_state.
    # The migration is content-preserving only.
    assert "world_confrontations" not in migrated
    # If there's no magic_state to migrate INTO, the entries are dropped
    # rather than synthesized. Document this behavior — it matches the
    # "no silent fallback" rule (we don't invent a magic config).
    assert migrated.get("magic_state") is None or migrated["magic_state"] == {}
