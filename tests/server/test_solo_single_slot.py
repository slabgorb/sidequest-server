"""Integration: solo game slug rejects second connection.

Verifies that when a SOLO-mode game is connected to, a second player
connecting with the same slug receives a SoloSlotConflict error message.

Task 2 (commit 85d70f9) wired SoloSlotConflict → ERROR into the slug-connect
branch of session_handler. This test confirms the wiring works end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.app import create_app

# Real genre/world available in the content repo
_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault"


def _seed_game(save_dir: Path, slug: str, mode: str) -> None:
    """Seed a game row into the SQLite database."""
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


def test_second_connection_to_solo_is_rejected(tmp_path: Path):
    """Verify that a second player connecting to a SOLO game is rejected.

    Flow:
    1. Seed a SOLO-mode game row for slug
    2. First WebSocket (alice) connects → drain SESSION_EVENT{connected}
    3. Second WebSocket (bob) connects with same slug → expect ERROR with 'solo'
    """
    # Find the actual genre_packs directory
    genre_packs_path: Path | None = next(
        (p for p in DEFAULT_GENRE_PACK_SEARCH_PATHS if p.exists()),
        None,
    )
    if genre_packs_path is None:
        pytest.skip(
            f"No genre_packs directory found in {DEFAULT_GENRE_PACK_SEARCH_PATHS}"
        )

    # Seed the game as SOLO mode
    _seed_game(tmp_path, _SLUG, "solo")

    # Create app
    app = create_app(
        genre_pack_search_paths=[genre_packs_path],
        save_dir=tmp_path,
    )
    client = TestClient(app)

    # First connection: alice
    with client.websocket_connect("/ws") as alice_ws:
        alice_ws.send_json(
            {
                "type": "SESSION_EVENT",
                "player_id": "alice",
                "payload": {"event": "connect", "game_slug": _SLUG},
            }
        )
        # Drain SESSION_EVENT{connected}
        msg = alice_ws.receive_json()
        assert msg["type"] == "SESSION_EVENT"
        assert msg["payload"]["event"] == "connected"

        # Second connection: bob (same slug, while alice is still connected)
        with client.websocket_connect("/ws") as bob_ws:
            bob_ws.send_json(
                {
                    "type": "SESSION_EVENT",
                    "player_id": "bob",
                    "payload": {"event": "connect", "game_slug": _SLUG},
                }
            )
            # Expect ERROR message with "solo" in the message
            error_msg = bob_ws.receive_json()
            assert error_msg["type"] == "ERROR", (
                f"Expected ERROR message, got {error_msg['type']}"
            )
            error_payload = error_msg.get("payload", {})
            error_message = error_payload.get("message", "")
            assert "solo" in error_message.lower(), (
                f"Expected 'solo' in error message, got: {error_message}"
            )
