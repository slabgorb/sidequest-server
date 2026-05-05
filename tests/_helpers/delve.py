"""Test helpers for the Sünden delve-lifecycle handler suite.

These are direct-store manipulators used by Task 8's DUNGEON_SELECT tests
and by future tasks (RETREAT_TO_HAMLET, recruit/dismiss endpoints).
``drive_recruit`` and ``drive_dismiss`` will be reimplemented in Task 11
once the recruit REST endpoint exists; the signatures stay stable so
test bodies don't churn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.world_save import Hireling
from sidequest.protocol.messages import (
    DungeonSelectMessage,
    DungeonSelectPayload,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry


def seed_hub_game(
    save_dir: Path,
    slug: str,
    *,
    genre: str = "caverns_and_claudes",
    world: str = "caverns_three_sins",
    mode: GameMode = GameMode.SOLO,
) -> None:
    """Create a clean hub-mode game row + initialized DB."""
    db = db_path_for_slug(save_dir, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=mode,
        genre_slug=genre,
        world_slug=world,
    )
    store.close()


def drive_recruit(
    save_dir: Path,
    slug: str,
    *,
    hireling_id: str,
    name: str,
    archetype: str = "prig",
    status: str = "active",
) -> Hireling:
    """Add a Hireling directly to WorldSave.roster.

    Returns the Hireling. Will be reimplemented as a REST call in Task 11
    once the recruit endpoint lands.
    """
    db = db_path_for_slug(save_dir, slug)
    store = SqliteStore(db)
    store.initialize()
    try:
        ws = store.load_world_save()
        h = Hireling(
            id=hireling_id,
            name=name,
            archetype=archetype,
            status=status,  # type: ignore[arg-type]
        )
        store.save_world_save(ws.model_copy(update={"roster": [*ws.roster, h]}))
        return h
    finally:
        store.close()


def drive_dismiss(
    save_dir: Path,
    slug: str,
    *,
    hireling_id: str,
    reason: str = "died_offscreen",
) -> None:
    """Mark a roster Hireling as dead via direct WorldSave manipulation.

    Reason is purely the test-side framing — at the data layer this maps
    to ``status="dead"``. Task 11 will reimplement this against the
    dismiss REST endpoint.
    """
    del reason  # documented above; not used at the data layer
    db = db_path_for_slug(save_dir, slug)
    store = SqliteStore(db)
    store.initialize()
    try:
        ws = store.load_world_save()
        new_roster = []
        for h in ws.roster:
            if h.id == hireling_id:
                new_roster.append(h.model_copy(update={"status": "dead"}))
            else:
                new_roster.append(h)
        store.save_world_save(ws.model_copy(update={"roster": new_roster}))
    finally:
        store.close()


def make_handler(
    save_dir: Path,
    *,
    search_paths: list[Path],
    socket_id: str = "sock-delve-test",
) -> WebSocketSessionHandler:
    """Construct + attach a WebSocket handler for delve-lifecycle tests."""
    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=search_paths,
    )
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id=socket_id,
        out_queue=asyncio.Queue(),
    )
    return handler


async def drive_connect(
    handler: WebSocketSessionHandler,
    slug: str,
    *,
    player_id: str = "alice",
    player_name: str = "Alice",
) -> list[object]:
    """Drive a SESSION_EVENT{connect} through the handler. Returns outbound msgs."""
    return await handler.handle_message(
        SessionEventMessage(
            type="SESSION_EVENT",  # type: ignore[arg-type]
            player_id=player_id,
            payload=SessionEventPayload(
                event="connect",
                game_slug=slug,
                player_name=player_name,
            ),
        )
    )


async def drive_dungeon_select(
    handler: WebSocketSessionHandler,
    *,
    dungeon: str,
    party_hireling_ids: list[str],
    player_id: str = "alice",
) -> list[object]:
    """Drive a DUNGEON_SELECT through the dispatcher. Returns outbound msgs."""
    return await handler.handle_message(
        DungeonSelectMessage(
            type="DUNGEON_SELECT",  # type: ignore[arg-type]
            player_id=player_id,
            payload=DungeonSelectPayload(
                dungeon=dungeon,
                party_hireling_ids=party_hireling_ids,
            ),
        )
    )
