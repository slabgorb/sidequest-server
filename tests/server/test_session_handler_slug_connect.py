"""Tests for WebSocket connect accepting game_slug (Task 4, MP-01).

Verifies that SESSION_EVENT{connect} with a game_slug field:
- loads the game from the slug-based SQLite store and emits SESSION_CONNECTED
- emits ERROR when the slug doesn't correspond to a known game
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.protocol.messages import SessionEventMessage, SessionEventPayload
from sidequest.server.session_handler import WebSocketSessionHandler


@pytest.fixture
def seeded_game(tmp_path: Path) -> Path:
    slug = "2026-04-22-moldharrow-keep"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug="low_fantasy", world_slug="moldharrow-keep")
    return tmp_path


@pytest.mark.asyncio
async def test_connect_by_slug_loads_existing_game(seeded_game: Path):
    handler = WebSocketSessionHandler(save_dir=seeded_game)
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug="2026-04-22-moldharrow-keep",
        ),
    )
    outbound = await handler.handle_message(msg)
    assert any(getattr(m, "type", None) == "SESSION_EVENT" for m in outbound), (
        f"Expected SESSION_EVENT(connected) in outbound, got: {[getattr(m, 'type', None) for m in outbound]}"
    )
    # Verify the connected event has event="connected"
    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, (
        f"Expected SESSION_EVENT{{connected}} in outbound, got: {outbound}"
    )
    assert handler.session_data is not None
    assert handler.session_data.game_slug == "2026-04-22-moldharrow-keep"
    assert handler.session_data.mode == GameMode.MULTIPLAYER


@pytest.mark.asyncio
async def test_connect_by_unknown_slug_errors(seeded_game: Path):
    handler = WebSocketSessionHandler(save_dir=seeded_game)
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug="2020-01-01-nowhere",
        ),
    )
    outbound = await handler.handle_message(msg)
    assert any(getattr(m, "type", None) == "ERROR" for m in outbound), (
        f"Expected ERROR in outbound, got: {[getattr(m, 'type', None) for m in outbound]}"
    )
