import sqlite3
from pathlib import Path

from sidequest.game.forensic_query import list_saves
from sidequest.game.persistence import SqliteStore


def _make_save(saves_dir: Path, slug: str, *, genre: str, world: str) -> SqliteStore:
    db = saves_dir / "games" / slug / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore.open(str(db))
    conn = store.connection()
    conn.execute(
        "INSERT OR REPLACE INTO session_meta "
        "(id, genre_slug, world_slug, created_at, last_played, schema_version) "
        "VALUES (1, ?, ?, ?, ?, 1)",
        (genre, world, "2026-05-18T00:00:00+00:00", "2026-05-18T00:05:00+00:00"),
    )
    conn.commit()
    return store


def test_list_saves_returns_meta_for_each_save(tmp_path):
    saves = tmp_path / "saves"
    s = _make_save(saves, "caverns_and_claudes_test", genre="caverns_and_claudes", world="test")
    s.close()
    result = list_saves(saves)
    assert len(result) == 1
    row = result[0]
    assert row["slug"] == "caverns_and_claudes_test"
    assert row["genre"] == "caverns_and_claudes"
    assert row["world"] == "test"
    assert "last_activity_ts" in row


def test_list_saves_skips_broken_db_loudly(tmp_path, caplog):
    saves = tmp_path / "saves"
    s = _make_save(saves, "good_save", genre="g", world="w")
    s.close()
    broken = saves / "games" / "broken_save" / "save.db"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("this is not sqlite")
    with caplog.at_level("WARNING"):
        result = list_saves(saves)
    slugs = {r["slug"] for r in result}
    assert slugs == {"good_save"}
    assert "broken_save" in caplog.text


def test_list_saves_missing_root_returns_empty(tmp_path):
    assert list_saves(tmp_path / "nope") == []


def test_list_saves_does_not_mutate_the_save(tmp_path):
    """list_saves must read without writing. SqliteStore.open re-runs the
    full schema (executescript materializes every missing table) and flips
    journal_mode=WAL — a write to the main save.db. The read-only path must
    leave the file byte-identical. Seed via plain sqlite3 in DELETE mode
    (NOT SqliteStore) so the buggy path's schema-creation write is real and
    observable, and there is no pre-existing WAL-header confound."""
    saves = tmp_path / "saves"
    db = saves / "games" / "ro_test" / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.executescript(
        "PRAGMA journal_mode=DELETE;"
        "CREATE TABLE session_meta ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " genre_slug TEXT NOT NULL, world_slug TEXT NOT NULL,"
        " created_at TEXT NOT NULL, last_played TEXT NOT NULL,"
        " schema_version INTEGER NOT NULL DEFAULT 1);"
        "INSERT INTO session_meta VALUES"
        " (1,'g','w','2026-05-18T00:00:00+00:00','2026-05-18T00:05:00+00:00',1);"
    )
    con.commit()
    con.close()
    bytes_before = db.read_bytes()
    mtime_before = db.stat().st_mtime_ns

    result = list_saves(saves)

    assert {r["slug"] for r in result} == {"ro_test"}     # save is readable
    assert db.read_bytes() == bytes_before                # main db not rewritten
    assert db.stat().st_mtime_ns == mtime_before          # main db not touched


def test_list_saves_skips_save_with_no_meta_loudly(tmp_path, caplog):
    saves = tmp_path / "saves"
    db = saves / "games" / "no_meta_save" / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE session_meta ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " genre_slug TEXT NOT NULL, world_slug TEXT NOT NULL,"
        " created_at TEXT NOT NULL, last_played TEXT NOT NULL,"
        " schema_version INTEGER NOT NULL DEFAULT 1)"
    )  # table exists, but NO id=1 row inserted
    con.commit()
    con.close()
    with caplog.at_level("WARNING"):
        result = list_saves(saves)
    assert {r["slug"] for r in result} == set()  # skipped, not returned
    assert "forensic_query.no_meta" in caplog.text
    assert "no_meta_save" in caplog.text
