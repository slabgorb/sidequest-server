"""Wiring test: connecting to a hub world fails loudly with a typed error.

The genre-loader-dungeon-recursion plan (2026-05-04) added a hub-rejection
guard in the slug-connect path before the openings check. This test is the
*only* wiring confirmation the loader plan ships — every other test in
``tests/server/`` that would exercise this codepath is currently broken on
the deleted-world-slugs (grimvault/horden/mawdeep/dungeon_survivor/
primetime) and is owned by the follow-on test-sweep plan.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.game.persistence import GameMode, SqliteStore, db_path_for_slug, upsert_game
from sidequest.protocol.messages import SessionEventMessage, SessionEventPayload
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"
_SLUG = "2026-05-04-hub-guard-test"

_CONTENT_SEARCH_PATH = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def _make_handler(save_dir: Path) -> WebSocketSessionHandler:
    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-hub-guard",
        out_queue=asyncio.Queue(),
    )
    return handler


@pytest.fixture
def seeded_hub_game(tmp_path: Path) -> Path:
    """Seed a save row pointing at the caverns_three_sins hub world.
    The pack itself is real on-disk content; only the save row is synthetic.
    """
    db = db_path_for_slug(tmp_path, _SLUG)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=_SLUG,
        mode=GameMode.SOLO,
        genre_slug=_GENRE,
        world_slug=_HUB_WORLD,
    )
    store.close()
    return tmp_path


@pytest.mark.asyncio
async def test_connecting_to_hub_world_returns_typed_error(seeded_hub_game: Path) -> None:
    """A SESSION_EVENT{connect} pointing at the hub world resolves through
    the connect handler and yields an ERROR with the expected error code.

    Critically: the rejection fires *before* the in-flight opening helper
    treats an empty hub openings list as 'world_or_openings_missing'. The
    user sees a clear 'pick a dungeon' message, not the generic missing-
    openings skip.
    """
    if not (_CONTENT_SEARCH_PATH / _GENRE / "worlds" / _HUB_WORLD).is_dir():
        pytest.skip("caverns_three_sins hub content not on disk")

    handler = _make_handler(seeded_hub_game)
    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=_SLUG,
            player_name="Alice",
        ),
    )
    outbound = await handler.handle_message(msg)

    error_msgs = [m for m in outbound if getattr(m, "type", None) == "ERROR"]
    assert error_msgs, (
        f"Expected ERROR in outbound when connecting to hub world; got: "
        f"{[getattr(m, 'type', None) for m in outbound]}"
    )

    # Typed error code (so the UI can branch on this rather than parsing the
    # message). The connect handler emits ``hub_world_requires_dungeon_selection``
    # at the new guard.
    err = error_msgs[0]
    code = getattr(getattr(err, "payload", None), "code", None)
    assert code == "hub_world_requires_dungeon_selection", (
        f"Expected code='hub_world_requires_dungeon_selection', got {code!r}. "
        "Either the hub guard regressed or the in-flight openings-skip "
        "branch is firing first (the bug the upstream-of-line-242 placement "
        "guards against)."
    )

    # The error mentions all three available dungeons so the player knows
    # which they should be picking from.
    text = str(getattr(getattr(err, "payload", None), "message", ""))
    for dungeon in ("grimvault", "horden", "mawdeep"):
        assert dungeon in text, f"hub guard message must list dungeon {dungeon!r}; got: {text}"
