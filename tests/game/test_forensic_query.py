import json
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


def _seed_rounds(store):
    """Round 1: 2 events + 1 narrative. Round 2: 1 event + 1 narrative.

    Uses production timestamp SHAPES (Spike F5.2): narrative_log.created_at
    is sqlite datetime('now') form 'YYYY-MM-DD HH:MM:SS'; events.created_at
    is Python .isoformat() 'YYYY-MM-DDTHH:MM:SS.ffffff+00:00'.
    """
    conn = store.connection()
    conn.execute(
        "INSERT INTO narrative_log (round_number, author, content, tags, created_at) "
        "VALUES (1, 'narrator', 'You enter the cave.', '[]', '2026-05-18 00:01:00')"
    )
    conn.commit()
    conn.execute(
        "INSERT INTO events (kind, payload_json, created_at) VALUES "
        "('NARRATION', ?, '2026-05-18T00:01:01.000000+00:00')",
        (json.dumps({"type": "NARRATION", "state_delta": {"location": "Cave"}}),),
    )
    conn.execute(
        "INSERT INTO events (kind, payload_json, created_at) VALUES "
        "('TURN_STATUS', ?, '2026-05-18T00:01:02.000000+00:00')",
        (json.dumps({"type": "TURN_STATUS", "state_delta": None}),),
    )
    conn.execute(
        "INSERT INTO narrative_log (round_number, author, content, tags, created_at) "
        "VALUES (2, 'narrator', 'A goblin lunges.', '[]', '2026-05-18 00:02:00')"
    )
    conn.execute(
        "INSERT INTO events (kind, payload_json, created_at) VALUES "
        "('NARRATION', ?, '2026-05-18T00:02:01.000000+00:00')",
        (json.dumps({"type": "NARRATION", "state_delta": {"location": "Hall"}}),),
    )
    conn.commit()


def test_build_timeline_buckets_events_by_round(tmp_path):
    from sidequest.game.forensic_query import _ro_connect, build_timeline

    saves = tmp_path / "saves"
    store = _make_save(saves, "tl_test", genre="g", world="w")
    _seed_rounds(store)
    store.close()
    db = saves / "games" / "tl_test" / "save.db"
    conn = _ro_connect(db)
    try:
        timeline = build_timeline(conn)
    finally:
        conn.close()

    assert [t["round"] for t in timeline] == [1, 2]
    r1, r2 = timeline
    assert r1["seq_start"] == 1 and r1["seq_end"] == 2
    assert r1["event_kind_counts"] == {"NARRATION": 1, "TURN_STATUS": 1}
    assert r1["narrative_authors"] == ["narrator"]
    assert r2["seq_start"] == 3 and r2["seq_end"] == 3
    assert r2["event_kind_counts"] == {"NARRATION": 1}


def test_build_turn_bundle_assembles_all_panels(tmp_path):
    from sidequest.game.forensic_query import _ro_connect, build_turn_bundle

    saves = tmp_path / "saves"
    store = _make_save(saves, "bundle_test", genre="g", world="w")
    _seed_rounds(store)
    c = store.connection()
    c.execute(
        "INSERT INTO projection_cache (event_seq, player_id, include, payload_json) "
        "VALUES (1, 'player1', 1, ?)",
        (json.dumps({"type": "NARRATION", "text": "You enter the cave."}),),
    )
    c.execute(
        "INSERT INTO scrapbook_entries "
        "(turn_id, scene_title, scene_type, location, image_url, narrative_excerpt, "
        " world_facts, npcs_present, render_status) "
        "VALUES (1, 'The Cave Mouth', 'exploration', 'Cave', NULL, 'You enter.', "
        " '[]', '[]', 'rendered')"
    )
    c.commit()
    store.close()
    db = saves / "games" / "bundle_test" / "save.db"
    conn = _ro_connect(db)
    try:
        bundle = build_turn_bundle(conn, 1)
    finally:
        conn.close()

    assert bundle["round"] == 1
    assert [n["content"] for n in bundle["narrative"]] == ["You enter the cave."]
    assert [e["seq"] for e in bundle["events"]] == [1, 2]
    assert bundle["events"][0]["kind"] == "NARRATION"
    assert bundle["derived"]["location"]["value"] == "Cave"
    assert bundle["derived"]["location"]["source_seqs"] == [1]
    assert bundle["unparseable_seqs"] == []
    assert bundle["projection"][0]["player_id"] == "player1"
    assert bundle["projection"][0]["include"] == 1
    assert bundle["scrapbook"][0]["scene_title"] == "The Cave Mouth"


def test_build_turn_bundle_unknown_round_is_empty_not_error(tmp_path):
    from sidequest.game.forensic_query import _ro_connect, build_turn_bundle

    saves = tmp_path / "saves"
    store = _make_save(saves, "empty_round", genre="g", world="w")
    _seed_rounds(store)
    store.close()
    db = saves / "games" / "empty_round" / "save.db"
    conn = _ro_connect(db)
    try:
        bundle = build_turn_bundle(conn, 999)
    finally:
        conn.close()
    assert bundle["round"] == 999
    assert bundle["narrative"] == []
    assert bundle["events"] == []
    assert bundle["derived"] == {}
    assert bundle["projection"] == []
    assert bundle["scrapbook"] == []
    assert bundle["unparseable_seqs"] == []


def test_build_turn_bundle_never_raises_on_corrupt_stored_json(tmp_path, caplog):
    from sidequest.game.forensic_query import _ro_connect, build_turn_bundle

    saves = tmp_path / "saves"
    store = _make_save(saves, "corrupt", genre="g", world="w")
    c = store.connection()
    c.execute(
        "INSERT INTO narrative_log (round_number, author, content, tags, created_at) "
        "VALUES (1, 'narrator', 'hi', '{not json', '2026-05-18 00:01:00')"
    )
    c.execute(
        "INSERT INTO events (kind, payload_json, created_at) VALUES "
        "('NARRATION', '{bad json', '2026-05-18T00:01:01.000000+00:00')"
    )
    c.execute(
        "INSERT INTO scrapbook_entries "
        "(turn_id, scene_title, scene_type, location, image_url, narrative_excerpt, "
        " world_facts, npcs_present, render_status) "
        "VALUES (1, 'S', 't', 'L', NULL, 'x', '{bad', '[]', 'rendered')"
    )
    c.commit()
    store.close()
    db = saves / "games" / "corrupt" / "save.db"
    conn = _ro_connect(db)
    try:
        with caplog.at_level("WARNING"):
            bundle = build_turn_bundle(conn, 1)  # must NOT raise
    finally:
        conn.close()

    assert bundle["events"][0]["payload"] == {"__unparseable__": "{bad json"}
    assert 1 in bundle["unparseable_seqs"]
    assert bundle["narrative"][0]["tags"] == []
    assert bundle["scrapbook"][0]["world_facts"] == []
    assert "forensic_query.unparseable_json_list" in caplog.text
