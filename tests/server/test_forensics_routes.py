import json
from pathlib import Path

from fastapi.testclient import TestClient

from sidequest.game.persistence import SqliteStore
from sidequest.server.app import create_app


def _client(tmp_path: Path) -> TestClient:
    packs = tmp_path / "genre_packs"
    packs.mkdir(parents=True, exist_ok=True)
    saves = tmp_path / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    app = create_app(genre_pack_search_paths=[packs], save_dir=saves)
    return TestClient(app)


def _seed(saves: Path, slug: str):
    db = saves / "games" / slug / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore.open(str(db))
    c = store.connection()
    c.execute(
        "INSERT OR REPLACE INTO session_meta "
        "(id, genre_slug, world_slug, created_at, last_played, schema_version) "
        "VALUES (1, 'caverns_and_claudes', 'test', "
        "'2026-05-18T00:00:00+00:00', '2026-05-18T00:05:00+00:00', 1)"
    )
    c.execute(
        "INSERT INTO narrative_log (round_number, author, content, tags, created_at) "
        "VALUES (1, 'narrator', 'You enter.', '[]', '2026-05-18 00:01:00')"
    )
    c.execute(
        "INSERT INTO events (kind, payload_json, created_at) VALUES "
        "('NARRATION', ?, '2026-05-18T00:01:01.000000+00:00')",
        (json.dumps({"type": "NARRATION", "state_delta": {"location": "Cave"}}),),
    )
    c.commit()
    store.close()


def test_list_saves_endpoint(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    _seed(saves, "caverns_and_claudes_test")
    client = _client(tmp_path)
    resp = client.get("/api/debug/saves")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["slug"] == "caverns_and_claudes_test"
    assert body[0]["genre"] == "caverns_and_claudes"


def test_timeline_endpoint(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    _seed(saves, "caverns_and_claudes_test")
    client = _client(tmp_path)
    resp = client.get("/api/debug/save/caverns_and_claudes_test/timeline")
    assert resp.status_code == 200
    assert resp.json()[0]["round"] == 1


def test_turn_bundle_endpoint(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    _seed(saves, "caverns_and_claudes_test")
    client = _client(tmp_path)
    resp = client.get("/api/debug/save/caverns_and_claudes_test/turn/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["round"] == 1
    assert body["derived"]["location"]["value"] == "Cave"


def test_turn_bundle_unknown_slug_is_empty_not_500(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/api/debug/save/nope/turn/1")
    assert resp.status_code == 200
    assert resp.json() == {"round": 1, "narrative": [], "events": [],
                           "derived": {}, "projection": [], "scrapbook": [],
                           "unparseable_seqs": []}


def test_turn_bundle_corrupt_save_is_empty_not_500(tmp_path):
    """D7.4: a present-but-corrupt save degrades to empty, never 500."""
    saves = tmp_path / "saves"
    db = saves / "games" / "corruptslug" / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("this is not a sqlite database")
    client = _client(tmp_path)
    resp = client.get("/api/debug/save/corruptslug/turn/1")
    assert resp.status_code == 200
    assert resp.json() == {"round": 1, "narrative": [], "events": [],
                           "derived": {}, "projection": [], "scrapbook": [],
                           "unparseable_seqs": []}


def test_timeline_unknown_slug_is_empty_not_500(tmp_path):
    resp = _client(tmp_path).get("/api/debug/save/nope/timeline")
    assert resp.status_code == 200
    assert resp.json() == []


def test_timeline_corrupt_save_is_empty_not_500(tmp_path):
    saves = tmp_path / "saves"
    db = saves / "games" / "corruptslug" / "save.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("this is not a sqlite database")
    resp = _client(tmp_path).get("/api/debug/save/corruptslug/timeline")
    assert resp.status_code == 200
    assert resp.json() == []


def test_forensics_route_is_wired_and_serves_html(tmp_path):
    """Mandatory wiring test: proves app.py registered the router and the
    static asset resolves — not merely that the module imports."""
    client = _client(tmp_path)
    resp = client.get("/forensics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Save Forensics" in resp.text
