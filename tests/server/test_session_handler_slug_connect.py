"""Tests for WebSocket connect accepting game_slug (Task 4, MP-01).

Verifies that SESSION_EVENT{connect} with a game_slug field:
- loads the game from the slug-based SQLite store and emits SESSION_CONNECTED
- emits ERROR when the slug doesn't correspond to a known game
- genre_pack is populated (not None) so PLAYER_ACTION doesn't crash
- a slug-connect for a session with a saved snapshot resumes rather than restarting
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.game.session import GameSnapshot
from sidequest.protocol.messages import (
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry


def _make_handler(save_dir: Path, search_paths: list[Path]) -> WebSocketSessionHandler:
    """Construct a handler with room-context wiring.

    Mirrors what ws_endpoint does: build the handler, then immediately call
    attach_room_context with a fresh RoomRegistry, a unique socket_id, and an
    asyncio.Queue for outbound messages. The slug-connect branch requires
    all three — there is no silent test-only bypass.
    """
    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=search_paths,
    )
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-test",
        out_queue=asyncio.Queue(),
    )
    return handler

# Use a genre pack that exists in the content repo.
_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault-test"

# Resolve the content search path relative to this file so tests work from
# any working directory.
# __file__ = oq-2/sidequest-server/tests/server/<file>.py
# parents[3] = oq-2 (orchestrator root)
_CONTENT_SEARCH_PATH = (
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
)


@pytest.fixture
def seeded_game(tmp_path: Path) -> Path:
    slug = _SLUG
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)
    store.close()
    return tmp_path


@pytest.mark.asyncio
async def test_connect_by_slug_loads_existing_game(seeded_game: Path):
    handler = _make_handler(seeded_game, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=_SLUG,
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
    assert handler.session_data.game_slug == _SLUG
    assert handler.session_data.mode == GameMode.MULTIPLAYER
    # Bug 1 regression: genre_pack must be a real GenrePack, never None.
    assert handler.session_data.genre_pack is not None, (
        "genre_pack must not be None after slug-connect — PLAYER_ACTION would crash"
    )


@pytest.mark.asyncio
async def test_connect_by_unknown_slug_errors(seeded_game: Path):
    handler = _make_handler(seeded_game, [_CONTENT_SEARCH_PATH])
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


@pytest.mark.asyncio
async def test_slug_connect_resumes_saved_snapshot(tmp_path: Path):
    """Bug 2 regression: slug-connect with a saved session restores it.

    Seeds a game row *and* a saved GameSnapshot with one character so
    has_character comes back True and state is Playing (not Creating).
    """
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory

    slug = "2026-04-22-resume-test"
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(store, slug=slug, mode=GameMode.MULTIPLAYER,
                genre_slug=_GENRE, world_slug=_WORLD)

    # Build a minimal character and save a snapshot that contains it.
    core = CreatureCore(
        name="Rux",
        description="A stoic fighter",
        personality="stoic",
        inventory=Inventory(),
    )
    char = Character(core=core, char_class="Fighter", race="Human", backstory="A wandering fighter")
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD, location="Entrance")
    snap.characters = [char]
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()

    handler = _make_handler(tmp_path, [_CONTENT_SEARCH_PATH])
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="rux-player",
        payload=SessionEventPayload(event="connect", game_slug=slug),
    )
    outbound = await handler.handle_message(msg)

    connected_msgs = [
        m for m in outbound
        if getattr(m, "type", None) == "SESSION_EVENT"
        and getattr(getattr(m, "payload", None), "event", None) == "connected"
    ]
    assert connected_msgs, f"Expected SESSION_EVENT(connected), got: {outbound}"

    connected_payload = connected_msgs[0].payload
    # has_character must reflect the saved snapshot, not hardcoded False.
    assert connected_payload.has_character is True, (
        "has_character should be True when the saved snapshot has a character"
    )
    sd = handler.session_data
    assert sd is not None
    assert sd.snapshot.characters, "Snapshot must carry the saved character after resume"
    assert sd.snapshot.characters[0].core.name == "Rux"


@pytest.mark.asyncio
async def test_slug_connect_without_room_context_raises(seeded_game: Path):
    """Wiring test: slug-connect must fail loudly when attach_room_context was skipped.

    Regression test for the removed `hasattr(self, "_room_registry")` silent
    fallback. Any code path that reaches slug-connect without the WebSocket
    lifecycle having called attach_room_context() is a wiring bug — the
    handler must refuse to proceed, not silently skip room registration.
    """
    handler = WebSocketSessionHandler(
        save_dir=seeded_game,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    # Deliberately do NOT call attach_room_context.
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(event="connect", game_slug=_SLUG),
    )
    with pytest.raises(RuntimeError, match="attach_room_context"):
        await handler.handle_message(msg)
