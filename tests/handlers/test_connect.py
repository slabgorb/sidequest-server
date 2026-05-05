"""Connect handler — Sünden engine plan Task 7.

Verifies the hub-world connect path now emits a HUB_VIEW message instead
of a typed-error rejection. The three tests here lock the new contract:

1. Fresh hub-world connect → exactly one HUB_VIEW, no NARRATION, no ERROR.
2. Mid-delve resume (snapshot has ``active_delve_dungeon`` set) → no
   HUB_VIEW; behaves like the leaf-world resume path.
3. Leaf-world connect → unchanged (no HUB_VIEW, regular CHARACTER_CREATION
   or NARRATION flow).

Real ``sidequest-content`` is required because the HUB_VIEW dungeons list
is built from on-disk world content (sin resolution from
``Dungeon.config.sin``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.session import GameSnapshot
from sidequest.protocol.messages import (
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry

_GENRE = "caverns_and_claudes"
_HUB_WORLD = "caverns_three_sins"
_LEAF_WORLD_GENRE = "space_opera"
_LEAF_WORLD = "coyote_star"

_CONTENT_SEARCH_PATH = (
    Path(__file__).resolve().parents[2].parent / "sidequest-content" / "genre_packs"
)


def _make_handler(save_dir: Path) -> WebSocketSessionHandler:
    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=[_CONTENT_SEARCH_PATH],
    )
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-connect-test",
        out_queue=asyncio.Queue(),
    )
    return handler


def _seed_fresh_hub_game(save_dir: Path, slug: str) -> None:
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


def _seed_mid_delve_hub_game(save_dir: Path, slug: str) -> None:
    """Hub-world game with a snapshot mid-delve (active_delve_dungeon set)."""
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
    char = Character(
        core=CreatureCore(
            name="Rux",
            description="A roster member of caverns_three_sins.",
            personality="Plain-spoken.",
            inventory=Inventory(),
        ),
        char_class="Adventurer",
        race="Human",
        backstory="Recruited from the hamlet.",
        hireling_id="hireling-rux-001",
    )
    snap = GameSnapshot(
        genre_slug=_GENRE,
        world_slug=_HUB_WORLD,
        location="Grimvault Threshold",
        active_delve_dungeon="grimvault",
    )
    snap.characters = [char]
    store.init_session(_GENRE, _HUB_WORLD)
    store.save(snap)
    store.close()


def _seed_fresh_leaf_game(save_dir: Path, slug: str) -> None:
    db = db_path_for_slug(save_dir, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=GameMode.SOLO,
        genre_slug=_LEAF_WORLD_GENRE,
        world_slug=_LEAF_WORLD,
    )
    store.close()


def _content_available(genre: str, world: str) -> bool:
    return (_CONTENT_SEARCH_PATH / genre / "worlds" / world).is_dir()


@pytest.mark.asyncio
async def test_connect_emits_hub_view_for_hub_world(tmp_path: Path) -> None:
    """Fresh hub-world connect emits exactly one HUB_VIEW; no NARRATION/ERROR.

    The HUB_VIEW's available_dungeons list must contain the three Sünden
    dungeons (grimvault/horden/mawdeep), each with the correct sin
    resolved server-side from Dungeon.config.sin, and wounded=False on
    a fresh save (no entries in WorldSave.dungeon_wounds).
    """
    if not _content_available(_GENRE, _HUB_WORLD):
        pytest.skip("caverns_three_sins hub content not on disk")

    slug = "test-hub-fresh"
    _seed_fresh_hub_game(tmp_path, slug)
    handler = _make_handler(tmp_path)

    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=slug,
            player_name="Alice",
        ),
    )
    outbound = await handler.handle_message(msg)

    types = [getattr(m, "type", None) for m in outbound]
    hub_views = [m for m in outbound if getattr(m, "type", None) == "HUB_VIEW"]
    narrations = [m for m in outbound if getattr(m, "type", None) == "NARRATION"]
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]

    assert len(hub_views) == 1, (
        f"Expected exactly one HUB_VIEW; got types={types}"
    )
    assert not narrations, f"Expected no NARRATION on hub-mode connect; got types={types}"
    assert not errors, (
        f"Expected no ERROR on hub-mode connect; got types={types}, "
        f"errors={[getattr(getattr(e, 'payload', None), 'message', '') for e in errors]}"
    )

    payload = hub_views[0].payload
    assert payload.slug == slug
    assert payload.genre_slug == _GENRE
    assert payload.world_slug == _HUB_WORLD

    by_slug = {d.slug: d for d in payload.available_dungeons}
    assert set(by_slug.keys()) == {"grimvault", "horden", "mawdeep"}
    assert by_slug["grimvault"].sin == "pride"
    assert by_slug["horden"].sin == "greed"
    assert by_slug["mawdeep"].sin == "gluttony"
    for d in payload.available_dungeons:
        assert d.wounded is False, f"fresh save: {d.slug} should not be wounded"

    # Sorted by slug for deterministic UI order.
    assert [d.slug for d in payload.available_dungeons] == [
        "grimvault",
        "horden",
        "mawdeep",
    ]


@pytest.mark.asyncio
async def test_connect_resumes_mid_delve_skipping_hub_view(tmp_path: Path) -> None:
    """A snapshot with ``active_delve_dungeon`` set bypasses the hub branch.

    Hub-world resume mid-delve must NOT emit HUB_VIEW; the player is
    already in a delve and the connect handler should drive the standard
    resume path (no error, no hub frame).
    """
    if not _content_available(_GENRE, _HUB_WORLD):
        pytest.skip("caverns_three_sins hub content not on disk")

    slug = "test-hub-mid-delve"
    _seed_mid_delve_hub_game(tmp_path, slug)
    handler = _make_handler(tmp_path)

    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=slug,
            player_name="Alice",
        ),
    )
    outbound = await handler.handle_message(msg)

    types = [getattr(m, "type", None) for m in outbound]
    hub_views = [m for m in outbound if getattr(m, "type", None) == "HUB_VIEW"]
    errors = [m for m in outbound if getattr(m, "type", None) == "ERROR"]

    assert not hub_views, (
        f"Mid-delve resume must NOT emit HUB_VIEW; got types={types}"
    )
    assert not errors, (
        f"Mid-delve resume must not error; got "
        f"errors={[getattr(getattr(e, 'payload', None), 'message', '') for e in errors]}"
    )


@pytest.mark.asyncio
async def test_connect_leaf_world_unchanged(tmp_path: Path) -> None:
    """Leaf-world (non-hub) connect remains unchanged: no HUB_VIEW.

    Regression guard: the new hub branch must be gated on ``is_hub_world``
    and not affect leaf worlds. Leaf-world fresh connect lands on
    chargen / opening narration, never HUB_VIEW.
    """
    if not _content_available(_LEAF_WORLD_GENRE, _LEAF_WORLD):
        pytest.skip("space_opera/coyote_star content not on disk")

    slug = "test-leaf-fresh"
    _seed_fresh_leaf_game(tmp_path, slug)
    handler = _make_handler(tmp_path)

    msg = SessionEventMessage(
        type="SESSION_EVENT",
        player_id="alice",
        payload=SessionEventPayload(
            event="connect",
            game_slug=slug,
            player_name="Alice",
        ),
    )
    outbound = await handler.handle_message(msg)

    types = [getattr(m, "type", None) for m in outbound]
    hub_views = [m for m in outbound if getattr(m, "type", None) == "HUB_VIEW"]
    assert not hub_views, (
        f"Leaf-world connect must NOT emit HUB_VIEW; got types={types}"
    )
    # At least one of CHARACTER_CREATION or NARRATION must be present —
    # the connect either drives chargen (fresh) or resumes into narration.
    assert any(t in {"CHARACTER_CREATION", "NARRATION"} for t in types), (
        f"Expected CHARACTER_CREATION or NARRATION on leaf-world connect; got types={types}"
    )
