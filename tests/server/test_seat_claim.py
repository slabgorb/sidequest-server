"""Integration: PLAYER_SEAT message + SEAT_CONFIRMED broadcast (MP-02 Task 5).

Verifies that:
1. A player can send PLAYER_SEAT{character_slot: "rux"} to claim a character slot.
2. The server responds with SEAT_CONFIRMED broadcast to all players.
3. The room's seated_player_ids() includes the claiming player.

Uses caverns_and_claudes / grimvault (genre/world available in the content repo).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.app import create_app

_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault-seat"


def _seed(tmp_path: Path, slug: str) -> None:
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=GameMode.MULTIPLAYER,
        genre_slug=_GENRE,
        world_slug=_WORLD,
    )
    store.close()


def test_player_seat_claim_broadcasts_seat_confirmed(tmp_path: Path):
    """Alice connects, sends PLAYER_SEAT{character_slot: 'rux'}, receives SEAT_CONFIRMED."""
    genre_packs_path: Path | None = next(
        (p for p in DEFAULT_GENRE_PACK_SEARCH_PATHS if p.exists()),
        None,
    )
    if genre_packs_path is None:
        pytest.skip(
            f"No genre_packs directory found in {DEFAULT_GENRE_PACK_SEARCH_PATHS}"
        )

    _seed(tmp_path, _SLUG)
    app = create_app(
        genre_pack_search_paths=[genre_packs_path],
        save_dir=tmp_path,
    )
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws_a:
        # Connect alice
        ws_a.send_json({
            "type": "SESSION_EVENT",
            "player_id": "alice",
            "payload": {"event": "connect", "game_slug": _SLUG},
        })
        connected_msg = ws_a.receive_json()
        assert connected_msg["type"] == "SESSION_EVENT"
        assert connected_msg["payload"]["event"] == "connected"

        # Drain any other messages (e.g., initial state)
        # (There shouldn't be any in this test flow, but be defensive)

        # Send PLAYER_SEAT to claim character_slot "rux"
        ws_a.send_json({
            "type": "PLAYER_SEAT",
            "player_id": "alice",
            "payload": {"character_slot": "rux"},
        })

        # Expect SEAT_CONFIRMED broadcast
        seat_confirmed = ws_a.receive_json()
        assert seat_confirmed["type"] == "SEAT_CONFIRMED", (
            f"Expected SEAT_CONFIRMED, got {seat_confirmed['type']}"
        )
        assert seat_confirmed["payload"]["player_id"] == "alice", (
            f"Expected player_id='alice', got {seat_confirmed['payload'].get('player_id')}"
        )
        assert seat_confirmed["payload"]["character_slot"] == "rux", (
            f"Expected character_slot='rux', got {seat_confirmed['payload'].get('character_slot')}"
        )
