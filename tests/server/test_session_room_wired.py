"""Wiring test: RoomRegistry is attached to app.state and WebSocket lifecycle.

Verifies that:
1. `app.state.room_registry` exists after `create_app()`
2. A WebSocket connect via game_slug adds the player to the room
3. On disconnect the player is removed from the room

Uses caverns_and_claudes / grimvault (same as other wiring tests) since
low_fantasy is not present in the content repo search paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.app import create_app

# Real genre/world available in the content repo
_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault"


def _seed_game(save_dir: Path, slug: str, mode: str) -> None:
    from sidequest.game.persistence import (
        GameMode,
        SqliteStore,
        db_path_for_slug,
        upsert_game,
    )

    db = db_path_for_slug(save_dir, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=GameMode(mode),
        genre_slug=_GENRE,
        world_slug=_WORLD,
    )
    store.close()


def test_connecting_adds_player_to_room(tmp_path: Path):
    """Connect by slug → player in room; disconnect → player gone."""
    genre_packs_path: Path | None = next(
        (p for p in DEFAULT_GENRE_PACK_SEARCH_PATHS if p.exists()),
        None,
    )
    if genre_packs_path is None:
        pytest.skip(
            f"No genre_packs directory found in {DEFAULT_GENRE_PACK_SEARCH_PATHS}"
        )

    app = create_app(
        genre_pack_search_paths=[genre_packs_path],
        save_dir=tmp_path,
    )
    _seed_game(tmp_path, _SLUG, "multiplayer")

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "SESSION_EVENT",
                "player_id": "alice",
                "payload": {"event": "connect", "game_slug": _SLUG},
            }
        )
        ws.receive_json()  # drain SESSION_CONNECTED

        room = app.state.room_registry.get(_SLUG)
        assert room is not None, "Room must be created after slug-connect"
        assert "alice" in room.connected_player_ids(), (
            f"alice must appear in connected_player_ids(); got {room.connected_player_ids()}"
        )

    # Socket closed — player should be removed
    room = app.state.room_registry.get(_SLUG)
    assert room is not None, "Room must still exist after disconnect"
    assert "alice" not in room.connected_player_ids(), (
        f"alice must be removed on disconnect; got {room.connected_player_ids()}"
    )
