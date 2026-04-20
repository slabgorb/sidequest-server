"""Unit tests for sidequest.server.rest endpoints.

Tests /api/genres, /api/saves, /api/saves/new, DELETE /api/saves/...,
and /api/sessions.

No real genre pack files needed — tests use tmp_path fixtures and minimal
YAML stubs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sidequest.server.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_mock_genre_pack(packs_dir: Path, genre_slug: str, world_slug: str) -> None:
    """Write minimal pack.yaml + world/world.yaml under packs_dir."""
    genre_dir = packs_dir / genre_slug
    genre_dir.mkdir(parents=True, exist_ok=True)

    # pack.yaml
    (genre_dir / "pack.yaml").write_text(
        yaml.dump(
            {
                "name": f"{genre_slug.replace('_', ' ').title()}",
                "description": f"Test description for {genre_slug}",
                "code": genre_slug,
                "version": "1.0",
                "genre": genre_slug,
                "system": "generic",
                "intended_audience": "all",
                "content_warnings": [],
                "tags": [],
            }
        ),
        encoding="utf-8",
    )

    # worlds/world_slug/world.yaml
    world_dir = genre_dir / "worlds" / world_slug
    world_dir.mkdir(parents=True, exist_ok=True)
    (world_dir / "world.yaml").write_text(
        yaml.dump(
            {
                "name": f"{world_slug.replace('_', ' ').title()}",
                "description": f"A world called {world_slug}",
                "starting_location": "Town Square",
                "era": "1878",
                "setting": "The frontier",
                "inspirations": ["Tombstone", "High Noon"],
                "axis_snapshot": {"tension": 0.4, "mystery": 0.6},
            }
        ),
        encoding="utf-8",
    )


def _make_app(tmp_path: Path) -> TestClient:
    packs_dir = tmp_path / "genre_packs"
    packs_dir.mkdir()
    _create_mock_genre_pack(packs_dir, "spaghetti_western", "dust_and_lead")
    _create_mock_genre_pack(packs_dir, "caverns_and_claudes", "flickering_reach")

    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()

    app = create_app(
        genre_pack_search_paths=[packs_dir],
        save_dir=saves_dir,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/genres
# ---------------------------------------------------------------------------


def test_list_genres_returns_dict(tmp_path):
    """GET /api/genres returns a dict keyed by genre slug."""
    client = _make_app(tmp_path)
    resp = client.get("/api/genres")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


def test_list_genres_contains_expected_genres(tmp_path):
    """GET /api/genres includes genres from the mock packs directory."""
    client = _make_app(tmp_path)
    data = client.get("/api/genres").json()
    assert "spaghetti_western" in data
    assert "caverns_and_claudes" in data


def test_list_genres_has_name_and_description(tmp_path):
    """Genre entries have name and description fields."""
    client = _make_app(tmp_path)
    data = client.get("/api/genres").json()
    genre = data["spaghetti_western"]
    assert "name" in genre
    assert "description" in genre
    assert genre["name"] == "Spaghetti Western"


def test_list_genres_has_worlds(tmp_path):
    """Genre entries include a worlds list."""
    client = _make_app(tmp_path)
    data = client.get("/api/genres").json()
    worlds = data["spaghetti_western"]["worlds"]
    assert isinstance(worlds, list)
    assert len(worlds) >= 1
    world = worlds[0]
    assert world["slug"] == "dust_and_lead"
    assert world["name"] == "Dust And Lead"
    assert world["era"] == "1878"
    assert world["setting"] == "The frontier"
    assert world["inspirations"] == ["Tombstone", "High Noon"]


def test_list_genres_empty_when_no_packs_dir(tmp_path):
    """GET /api/genres returns {} when no valid genre pack directories exist."""
    nonexistent = tmp_path / "no_such_dir"
    app = create_app(
        genre_pack_search_paths=[nonexistent],
        save_dir=tmp_path / "saves",
    )
    client = TestClient(app)
    data = client.get("/api/genres").json()
    assert data == {}


def test_list_genres_skips_bad_pack_yaml(tmp_path):
    """Broken pack.yaml is silently skipped (best-effort)."""
    packs_dir = tmp_path / "genre_packs"
    packs_dir.mkdir()
    _create_mock_genre_pack(packs_dir, "good_genre", "good_world")

    # Write a broken pack.yaml for a second genre
    bad_genre_dir = packs_dir / "broken_genre"
    bad_genre_dir.mkdir()
    (bad_genre_dir / "pack.yaml").write_text(
        "this: is: not: valid: yaml: [{{",
        encoding="utf-8",
    )

    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()

    app = create_app(genre_pack_search_paths=[packs_dir], save_dir=saves_dir)
    client = TestClient(app)
    data = client.get("/api/genres").json()
    # Good genre is present, broken one is absent
    assert "good_genre" in data
    assert "broken_genre" not in data


def test_list_genres_axis_snapshot_format(tmp_path):
    """axis_snapshot is a dict of str → float."""
    client = _make_app(tmp_path)
    data = client.get("/api/genres").json()
    snapshot = data["spaghetti_western"]["worlds"][0]["axis_snapshot"]
    assert isinstance(snapshot, dict)
    for k, v in snapshot.items():
        assert isinstance(k, str)
        assert isinstance(v, (int, float))


# ---------------------------------------------------------------------------
# GET /api/saves
# ---------------------------------------------------------------------------


def test_list_saves_empty_when_no_saves(tmp_path):
    """GET /api/saves returns empty list when save dir is empty."""
    client = _make_app(tmp_path)
    resp = client.get("/api/saves")
    assert resp.status_code == 200
    data = resp.json()
    assert data["saves"] == []


def test_list_saves_finds_created_save(tmp_path):
    """A save created via POST /api/saves/new appears in GET /api/saves."""
    client = _make_app(tmp_path)

    # Create a save
    body = {
        "genre_slug": "spaghetti_western",
        "world_slug": "dust_and_lead",
        "player_name": "rex",
    }
    post_resp = client.post("/api/saves/new", json=body)
    assert post_resp.status_code == 200

    # List saves
    list_resp = client.get("/api/saves")
    data = list_resp.json()
    saves = data["saves"]
    assert len(saves) == 1
    assert saves[0]["genre_slug"] == "spaghetti_western"
    assert saves[0]["world_slug"] == "dust_and_lead"
    assert saves[0]["player_name"] == "rex"


def test_list_saves_genre_filter(tmp_path):
    """GET /api/saves?genre=... filters by genre."""
    client = _make_app(tmp_path)

    # Create two saves in different genres
    client.post("/api/saves/new", json={"genre_slug": "spaghetti_western", "world_slug": "dust_and_lead", "player_name": "p1"})
    client.post("/api/saves/new", json={"genre_slug": "caverns_and_claudes", "world_slug": "flickering_reach", "player_name": "p2"})

    data = client.get("/api/saves?genre=spaghetti_western").json()
    saves = data["saves"]
    assert all(s["genre_slug"] == "spaghetti_western" for s in saves)
    assert len(saves) == 1


# ---------------------------------------------------------------------------
# POST /api/saves/new
# ---------------------------------------------------------------------------


def test_create_save_returns_db_path(tmp_path):
    """POST /api/saves/new returns db_path."""
    client = _make_app(tmp_path)
    resp = client.post(
        "/api/saves/new",
        json={"genre_slug": "spaghetti_western", "world_slug": "dust_and_lead", "player_name": "cowboy"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "db_path" in data
    assert Path(data["db_path"]).suffix == ".db"


def test_create_save_missing_genre_returns_400(tmp_path):
    """POST /api/saves/new without genre_slug returns 400."""
    client = _make_app(tmp_path)
    resp = client.post("/api/saves/new", json={"world_slug": "dust_and_lead", "player_name": "cowboy"})
    assert resp.status_code == 400


def test_create_save_missing_world_returns_400(tmp_path):
    """POST /api/saves/new without world_slug returns 400."""
    client = _make_app(tmp_path)
    resp = client.post("/api/saves/new", json={"genre_slug": "spaghetti_western", "player_name": "cowboy"})
    assert resp.status_code == 400


def test_create_save_creates_db_file(tmp_path):
    """POST /api/saves/new actually writes a .db file to disk."""
    client = _make_app(tmp_path)
    resp = client.post(
        "/api/saves/new",
        json={"genre_slug": "spaghetti_western", "world_slug": "dust_and_lead", "player_name": "cowboy"},
    )
    db_path = Path(resp.json()["db_path"])
    assert db_path.exists()
    assert db_path.is_file()


# ---------------------------------------------------------------------------
# DELETE /api/saves/{genre}/{world}/{player}
# ---------------------------------------------------------------------------


def test_delete_save_removes_file(tmp_path):
    """DELETE /api/saves/... removes the save file."""
    client = _make_app(tmp_path)
    client.post(
        "/api/saves/new",
        json={"genre_slug": "spaghetti_western", "world_slug": "dust_and_lead", "player_name": "cowboy"},
    )

    del_resp = client.delete("/api/saves/spaghetti_western/dust_and_lead/cowboy")
    assert del_resp.status_code == 200
    data = del_resp.json()
    assert data["deleted"] is True


def test_delete_nonexistent_save_returns_404(tmp_path):
    """DELETE /api/saves/... for missing save returns 404."""
    client = _make_app(tmp_path)
    resp = client.delete("/api/saves/no_genre/no_world/nobody")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_empty(tmp_path):
    """GET /api/sessions returns empty sessions list (Phase 1 single-player)."""
    client = _make_app(tmp_path)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"sessions": []}
