"""REST tests for the hub recruit + dismiss endpoints (Task 11).

Sünden engine plan items 4b + Task 11. Exercises the production write-side
endpoints:

  POST   /api/games/{slug}/hub/recruit
  DELETE /api/games/{slug}/hub/roster/{hireling_id}

These tests live under ``tests/handlers/`` rather than ``tests/server/``
because the latter currently has a circular-import collection failure
(``protocol.messages`` ↔ ``genre.archetype`` cycle introduced when
``WorldSave`` was lifted into the wire-message layer). The file is
otherwise structured identically to ``tests/server/test_rest_hub_endpoint.py``
— same TestClient + tmp_path + content-search-path skip pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.session import GameSnapshot
from sidequest.game.world_save import Hireling, WorldSave
from sidequest.server.app import create_app

_CONTENT_SEARCH_PATH = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


@pytest.fixture()
def content_client(tmp_path: Path) -> TestClient:
    if not _CONTENT_SEARCH_PATH.exists():
        pytest.skip("sidequest-content not on disk")
    app = create_app(
        save_dir=tmp_path,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    return TestClient(app)


def _seed_game(save_dir: Path, slug: str, genre: str, world: str) -> SqliteStore:
    db = db_path_for_slug(save_dir, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.SOLO,
                genre_slug=genre, world_slug=world)
    return store


# ---------------------------------------------------------------------------
# POST /api/games/{slug}/hub/recruit
# ---------------------------------------------------------------------------


def test_recruit_adds_hireling_to_roster(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """POST /hub/recruit returns a fresh active hireling and the next GET
    /hub shows roster size 1.
    """
    _seed_game(
        tmp_path, "cnc-recruit",
        "caverns_and_claudes", "caverns_three_sins",
    ).close()

    r = content_client.post("/api/games/cnc-recruit/hub/recruit")
    assert r.status_code == 200, r.text
    body = r.json()
    # Shape contract: id pattern, name non-empty, status=active, stress=0.
    assert isinstance(body["id"], str) and body["id"]
    assert isinstance(body["name"], str) and body["name"]
    assert body["status"] == "active"
    assert body["stress"] == 0
    # archetype is a slugified funnel name (lowercase, underscored).
    assert body["archetype"] and body["archetype"] == body["archetype"].lower()
    # Sünden funnels carry sin_origin → notes encodes it for the narrator.
    assert body["notes"].startswith("sin_origin: ")

    # Roster grows.
    follow = content_client.get("/api/games/cnc-recruit/hub")
    assert follow.status_code == 200
    roster = follow.json()["world_save"]["roster"]
    assert len(roster) == 1
    assert roster[0]["id"] == body["id"]


def test_recruit_rejects_during_delve(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """When the snapshot has ``active_delve_dungeon`` set, recruit is
    blocked with 409 ``delve_in_progress``."""
    store = _seed_game(
        tmp_path, "cnc-mid-delve",
        "caverns_and_claudes", "caverns_three_sins",
    )
    # Persist a snapshot in delve mode. The endpoint reads
    # ``store.load().snapshot.active_delve_dungeon``.
    store.save(GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_three_sins",
        active_delve_dungeon="grimvault",
    ))
    store.close()

    r = content_client.post("/api/games/cnc-mid-delve/hub/recruit")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "delve_in_progress"
    assert detail["active_dungeon"] == "grimvault"


def test_recruit_rejects_on_non_hub_world(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """Non-hub worlds (no dungeons configured) reject recruit with 409
    ``not_a_hub_world`` — same gate the GET /hub endpoint uses."""
    _seed_game(
        tmp_path, "spgo-recruit",
        "space_opera", "coyote_star",
    ).close()
    r = content_client.post("/api/games/spgo-recruit/hub/recruit")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_a_hub_world"


# ---------------------------------------------------------------------------
# DELETE /api/games/{slug}/hub/roster/{hireling_id}
# ---------------------------------------------------------------------------


def test_dismiss_removes_alive_hireling(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """DELETE with reason=dismiss removes the row entirely (default)."""
    store = _seed_game(
        tmp_path, "cnc-fire",
        "caverns_and_claudes", "caverns_three_sins",
    )
    store.save_world_save(WorldSave(
        roster=[Hireling(id="vol_1", name="Volga", archetype="prig")],
    ))
    store.close()

    r = content_client.delete(
        "/api/games/cnc-fire/hub/roster/vol_1?reason=dismiss"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["roster"] == []


def test_dismiss_died_offscreen_keeps_row_marked_dead(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """DELETE with reason=died_offscreen keeps the row but flips status
    to dead (Wall / drift bookkeeping path)."""
    store = _seed_game(
        tmp_path, "cnc-mourn",
        "caverns_and_claudes", "caverns_three_sins",
    )
    store.save_world_save(WorldSave(
        roster=[Hireling(id="vol_1", name="Volga", archetype="prig")],
    ))
    store.close()

    r = content_client.delete(
        "/api/games/cnc-mourn/hub/roster/vol_1?reason=died_offscreen"
    )
    assert r.status_code == 200, r.text
    roster = r.json()["roster"]
    assert len(roster) == 1
    assert roster[0]["id"] == "vol_1"
    assert roster[0]["status"] == "dead"


def test_dismiss_404_unknown_id(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """DELETE on an id not in the roster returns 404 hireling_not_found."""
    _seed_game(
        tmp_path, "cnc-ghost",
        "caverns_and_claudes", "caverns_three_sins",
    ).close()

    r = content_client.delete(
        "/api/games/cnc-ghost/hub/roster/does_not_exist?reason=dismiss"
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "hireling_not_found"


def test_dismiss_400_invalid_reason(
    content_client: TestClient, tmp_path: Path,
) -> None:
    """An unknown ``reason`` value rejects with 400 invalid_reason — the
    endpoint accepts only ``dismiss`` and ``died_offscreen``."""
    store = _seed_game(
        tmp_path, "cnc-badreason",
        "caverns_and_claudes", "caverns_three_sins",
    )
    store.save_world_save(WorldSave(
        roster=[Hireling(id="vol_1", name="Volga", archetype="prig")],
    ))
    store.close()

    r = content_client.delete(
        "/api/games/cnc-badreason/hub/roster/vol_1?reason=banished"
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_reason"
