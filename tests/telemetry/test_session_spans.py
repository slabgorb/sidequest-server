"""Five hub + delve lifecycle watcher events (Sünden plan Task 12).

The GM panel's lie detector for the delve engine: every transition in the
hub → delve → hub cycle MUST emit a watcher event so Sebastien (the
mechanics-first player) can verify the engine engaged. Without these, a
convincing narrator can describe a delve that never started.

Each test drives the production handler path end-to-end and captures the
watcher events through the per-module ``_watcher_publish`` alias. Using
the alias point (not the source ``publish_event``) is required because
each handler imports the function as ``from ... import publish_event as
_watcher_publish`` at module load — patching the source would not catch
the already-bound names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.world_save import Hireling, WorldSave
from sidequest.handlers.retreat_to_hamlet import maybe_end_delve_on_player_dead
from sidequest.server.app import create_app
from tests._helpers.delve import (
    drive_connect,
    drive_dungeon_select,
    drive_recruit,
    drive_retreat,
    make_handler,
    seed_hub_game,
)

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"
_DUNGEON = "grimvault"

_CONTENT_SEARCH_PATH = (
    Path(__file__).resolve().parents[2].parent
    / "sidequest-content"
    / "genre_packs"
)


def _content_available() -> bool:
    return (_CONTENT_SEARCH_PATH / _GENRE / "worlds" / _HUB_WORLD).is_dir()


pytestmark = pytest.mark.skipif(
    not _content_available(),
    reason="caverns_three_sins hub content not on disk",
)


CapturedEvent = tuple[str, dict[str, Any], dict[str, str]]


def _capture(monkeypatch, module_path: str) -> list[CapturedEvent]:
    """Patch the ``_watcher_publish`` alias on ``module_path`` and capture.

    Each handler/REST module aliases ``publish_event`` at import time, so
    a single source-level patch wouldn't intercept those bindings.
    """
    captured: list[CapturedEvent] = []

    def fake_publish(
        event_type: str,
        fields: dict[str, Any],
        *,
        component: str = "",
        severity: str = "info",
    ) -> None:
        captured.append(
            (event_type, fields, {"component": component, "severity": severity})
        )

    monkeypatch.setattr(f"{module_path}._watcher_publish", fake_publish)
    return captured


def _events_of_type(
    captured: list[CapturedEvent], event_type: str
) -> list[CapturedEvent]:
    return [e for e in captured if e[0] == event_type]


# ---------------------------------------------------------------------------
# Span 1 — session.hub_mode_entered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_mode_entered_span_fires_on_hub_connect(
    tmp_path: Path, monkeypatch
) -> None:
    slug = "span-hub-entered"
    seed_hub_game(tmp_path, slug)
    drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )

    captured = _capture(monkeypatch, "sidequest.handlers.connect")

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)

    matches = _events_of_type(captured, "session.hub_mode_entered")
    assert len(matches) == 1, (
        f"expected exactly one session.hub_mode_entered; "
        f"got {[e[0] for e in captured]}"
    )
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["genre"] == _GENRE
    assert fields["world"] == _HUB_WORLD
    assert fields["roster_size"] == 1
    assert fields["delve_count"] == 0
    assert meta["component"] == "session"


# ---------------------------------------------------------------------------
# Span 2 — session.delve_started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delve_started_span_fires(tmp_path: Path, monkeypatch) -> None:
    slug = "span-delve-started"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )
    h2 = drive_recruit(
        tmp_path, slug, hireling_id="brawler_002", name="Tor", archetype="brawler"
    )

    captured = _capture(monkeypatch, "sidequest.handlers.dungeon_select")

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)
    out = await drive_dungeon_select(
        handler, dungeon=_DUNGEON, party_hireling_ids=[h1.id, h2.id]
    )
    assert not [m for m in out if getattr(m, "type", None) == "ERROR"]

    matches = _events_of_type(captured, "session.delve_started")
    assert len(matches) == 1, f"expected one session.delve_started; got {captured}"
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["dungeon"] == _DUNGEON
    assert fields["party_size"] == 2
    assert list(fields["party_hireling_ids"]) == [h1.id, h2.id]
    assert meta["component"] == "session"


# ---------------------------------------------------------------------------
# Span 3 — session.delve_ended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delve_ended_span_fires_with_outcome(
    tmp_path: Path, monkeypatch
) -> None:
    slug = "span-delve-ended"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )

    captured = _capture(monkeypatch, "sidequest.handlers.retreat_to_hamlet")

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)
    await drive_dungeon_select(
        handler, dungeon=_DUNGEON, party_hireling_ids=[h1.id]
    )
    out = await drive_retreat(handler, outcome="retreat", wounded_boss=False)
    assert not [m for m in out if getattr(m, "type", None) == "ERROR"]

    matches = _events_of_type(captured, "session.delve_ended")
    assert len(matches) == 1, f"expected one session.delve_ended; got {captured}"
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["dungeon"] == _DUNGEON
    assert fields["outcome"] == "retreat"
    assert fields["party_size"] == 1
    assert fields["delve_count_after"] == 1
    assert meta["component"] == "session"


@pytest.mark.asyncio
async def test_delve_ended_span_fires_on_player_dead_defeat(
    tmp_path: Path, monkeypatch
) -> None:
    """player_dead auto-trigger emits delve_ended with outcome="defeat"."""
    slug = "span-delve-ended-defeat"
    seed_hub_game(tmp_path, slug)
    h1 = drive_recruit(
        tmp_path, slug, hireling_id="prig_001", name="Mira", archetype="prig"
    )

    captured = _capture(monkeypatch, "sidequest.handlers.retreat_to_hamlet")

    handler = make_handler(tmp_path, search_paths=[_CONTENT_SEARCH_PATH])
    await drive_connect(handler, slug)
    await drive_dungeon_select(
        handler, dungeon=_DUNGEON, party_hireling_ids=[h1.id]
    )

    # Simulate a narrator turn flipping player_dead True.
    snap = handler._room.snapshot
    assert snap.player_dead is False
    snap.player_dead = True
    out = await maybe_end_delve_on_player_dead(
        session=handler,
        slug=slug,
        prev_player_dead=False,
        snapshot=snap,
    )
    assert not [m for m in out if getattr(m, "type", None) == "ERROR"]

    matches = _events_of_type(captured, "session.delve_ended")
    assert len(matches) == 1, (
        f"expected one session.delve_ended; got {[e[0] for e in captured]}"
    )
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["dungeon"] == _DUNGEON
    assert fields["outcome"] == "defeat"
    assert fields["delve_count_after"] == 1
    assert meta["component"] == "session"


# ---------------------------------------------------------------------------
# Span 4 — session.hireling_recruited
# ---------------------------------------------------------------------------


def _seed_rest_game(save_dir: Path, slug: str) -> None:
    db = db_path_for_slug(save_dir, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=GameMode.SOLO,
        genre_slug=_GENRE,
        world_slug=_HUB_WORLD,
    )
    store.close()


def test_hireling_recruited_span_fires(tmp_path: Path, monkeypatch) -> None:
    slug = "span-recruit"
    _seed_rest_game(tmp_path, slug)

    captured = _capture(monkeypatch, "sidequest.server.rest")

    app = create_app(
        save_dir=tmp_path,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    client = TestClient(app)
    r = client.post(f"/api/games/{slug}/hub/recruit")
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    new_archetype = r.json()["archetype"]

    matches = _events_of_type(captured, "session.hireling_recruited")
    assert len(matches) == 1, (
        f"expected one session.hireling_recruited; got "
        f"{[e[0] for e in captured]}"
    )
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["hireling_id"] == new_id
    assert fields["archetype"] == new_archetype
    assert fields["roster_size_after"] == 1
    assert meta["component"] == "session"


# ---------------------------------------------------------------------------
# Span 5 — session.hireling_dismissed
# ---------------------------------------------------------------------------


def test_hireling_dismissed_span_fires(tmp_path: Path, monkeypatch) -> None:
    slug = "span-dismiss"
    _seed_rest_game(tmp_path, slug)

    # Pre-populate one hireling on the roster directly so the test can
    # focus on the dismiss-side emission (not the recruit one).
    db = db_path_for_slug(tmp_path, slug)
    store = SqliteStore(db)
    store.initialize()
    store.save_world_save(
        WorldSave(roster=[Hireling(id="vol_1", name="Volga", archetype="prig")])
    )
    store.close()

    captured = _capture(monkeypatch, "sidequest.server.rest")

    app = create_app(
        save_dir=tmp_path,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    client = TestClient(app)
    r = client.delete(
        f"/api/games/{slug}/hub/roster/vol_1?reason=died_offscreen"
    )
    assert r.status_code == 200, r.text

    matches = _events_of_type(captured, "session.hireling_dismissed")
    assert len(matches) == 1, (
        f"expected one session.hireling_dismissed; got "
        f"{[e[0] for e in captured]}"
    )
    _, fields, meta = matches[0]
    assert fields["slug"] == slug
    assert fields["hireling_id"] == "vol_1"
    assert fields["reason"] == "died_offscreen"
    # died_offscreen keeps the row (status flipped to dead), so size is unchanged.
    assert fields["roster_size_after"] == 1
    assert meta["component"] == "session"
