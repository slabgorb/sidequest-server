from unittest.mock import MagicMock

import pytest

from sidequest.game.persistence import GameMode
from sidequest.game.session import GameSnapshot
from sidequest.server.session_room import RoomRegistry, SessionRoom, SoloSlotConflict


def test_room_registry_returns_same_room_for_same_slug():
    reg = RoomRegistry()
    r1 = reg.get_or_create("slug-a", mode=GameMode.MULTIPLAYER)
    r2 = reg.get_or_create("slug-a", mode=GameMode.MULTIPLAYER)
    assert r1 is r2


def test_room_tracks_connected_players():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.connect("bob", socket_id="sock-2")
    assert set(room.connected_player_ids()) == {"alice", "bob"}


def test_room_disconnect_removes_player():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.disconnect(socket_id="sock-1")
    assert room.connected_player_ids() == []


def test_same_player_reconnect_updates_socket():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.connect("alice", socket_id="sock-1")
    room.connect("alice", socket_id="sock-2")
    room.disconnect(socket_id="sock-1")
    assert "alice" in room.connected_player_ids()  # sock-2 still holds alice


def test_solo_room_rejects_second_connection():
    room = SessionRoom(slug="slug-a", mode=GameMode.SOLO)
    room.connect("alice", socket_id="sock-1")
    with pytest.raises(SoloSlotConflict):
        room.connect("bob", socket_id="sock-2")


def test_solo_room_allows_same_player_reconnect():
    room = SessionRoom(slug="slug-a", mode=GameMode.SOLO)
    room.connect("alice", socket_id="sock-1")
    room.disconnect(socket_id="sock-1")
    room.connect("alice", socket_id="sock-2")  # must not raise
    assert room.connected_player_ids() == ["alice"]


def test_seated_players_separate_from_connected():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot="rux")
    room.seat("bob", character_slot="vex")
    room.connect("alice", socket_id="sock-1")
    # bob seated but not connected
    assert set(room.seated_player_ids()) == {"alice", "bob"}
    assert set(room.connected_player_ids()) == {"alice"}
    assert set(room.absent_seated_player_ids()) == {"bob"}


def test_slot_to_player_id_returns_seat_map():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot="Laverne")
    room.seat("bob", character_slot="Shirley")
    assert room.slot_to_player_id() == {"Laverne": "alice", "Shirley": "bob"}


def test_slot_to_player_id_skips_seats_without_slot():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    room.seat("alice", character_slot=None)  # legacy / pre-slot seat
    room.seat("bob", character_slot="Shirley")
    assert room.slot_to_player_id() == {"Shirley": "bob"}


def test_slot_to_player_id_empty_when_no_seats():
    room = SessionRoom(slug="slug-a", mode=GameMode.MULTIPLAYER)
    assert room.slot_to_player_id() == {}


# ---------------------------------------------------------------------------
# Canonical snapshot binding (ADR-037 Python port)
# ---------------------------------------------------------------------------


def _fresh_snapshot() -> GameSnapshot:
    return GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        location="Entrance",
    )


def test_bind_world_sets_snapshot_and_store_once():
    """First bind populates both fields; getters reflect them."""
    room = SessionRoom(slug="2026-04-25-test-mp", mode=GameMode.MULTIPLAYER)
    snap = _fresh_snapshot()
    store = MagicMock()

    assert room.snapshot is None
    assert room.store is None

    room.bind_world(snapshot=snap, store=store)

    assert room.snapshot is snap
    assert room.store is store


def test_bind_world_is_idempotent():
    """Second bind when already populated is a no-op (no overwrite, no raise).

    Guards against a race where two concurrent first-connects both try to
    bind. The first wins; the second silently observes the existing
    binding rather than stomping it.
    """
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    snap1 = _fresh_snapshot()
    store1 = MagicMock()
    snap2 = _fresh_snapshot()
    store2 = MagicMock()

    room.bind_world(snapshot=snap1, store=store1)
    room.bind_world(snapshot=snap2, store=store2)

    assert room.snapshot is snap1
    assert room.store is store1


def test_close_store_is_idempotent_and_calls_close_once():
    """close_store closes the bound store exactly once across N calls."""
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    store = MagicMock()
    room.bind_world(snapshot=_fresh_snapshot(), store=store)

    room.close_store()
    room.close_store()

    assert store.close.call_count == 1


def test_close_store_when_unbound_is_noop():
    """Pre-bind / never-bound rooms must not raise on close."""
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    room.close_store()  # must not raise


# ---------------------------------------------------------------------------
# Disconnect-save store-lifecycle invariant
# (playtest 2026-04-25 [BUG-LOW] "Cannot operate on a closed database")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_does_not_close_room_owned_store(tmp_path):
    """When the session is bound to a room, ``cleanup()`` must NOT close
    the underlying SqliteStore — the room owns the store lifecycle and
    the same store is shared with every other session bound to the slug.
    Closing it from one cleanup leaves ``room.save()`` operating on a
    closed connection from any other path's perspective and produces
    ``session.disconnect_save_failed error=Cannot operate on a closed
    database``.

    Regression for playtest 2026-04-25 [BUG-LOW]. The bug was triggered
    by trigger-the-confrontation-crash → return to lobby → server
    cleanup of the prior WS — the per-session ``store.close()`` ran in
    the finally block of cleanup, but the same store reference was
    reachable through the room's ``room.save()`` path (e.g., from a
    later turn-end save on a sibling connection or a disconnect-save
    from a peer player in MP).
    """
    from unittest.mock import AsyncMock, MagicMock

    from sidequest.game.persistence import SqliteStore
    from sidequest.server.session_handler import _SessionData, WebSocketSessionHandler

    handler = WebSocketSessionHandler(save_dir=tmp_path)
    snap = _fresh_snapshot()
    store = SqliteStore.open_in_memory()

    room = SessionRoom(slug="slug-cleanup", mode=GameMode.SOLO)
    room.bind_world(snapshot=snap, store=store)

    sd = _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        player_name="Rux",
        player_id="player-1",
        snapshot=snap,
        store=store,  # same store reference the room holds
        genre_pack=MagicMock(),
        orchestrator=MagicMock(run_narration_turn=AsyncMock()),
    )
    handler._session_data = sd
    handler._room = room

    await handler.cleanup()

    # The underlying SQLite connection must still be open — the room
    # owns the lifecycle, not the per-session cleanup.
    assert room.store is store, "room.store reference must not be None'd by cleanup"
    # Operating on the store after cleanup must succeed. If cleanup
    # closed the connection, ``room.save()`` raises sqlite3.ProgrammingError.
    room.save()  # must not raise


@pytest.mark.asyncio
async def test_cleanup_closes_per_session_store_when_no_room(tmp_path):
    """Legacy non-slug path: ``cleanup()`` must still close the
    per-session store when no room is bound — the session owns the
    store lifecycle in that path. Preserves the original close-on-
    disconnect behavior for any code path that hasn't been migrated to
    the room model.
    """
    from unittest.mock import AsyncMock, MagicMock

    from sidequest.game.persistence import SqliteStore
    from sidequest.server.session_handler import _SessionData, WebSocketSessionHandler

    handler = WebSocketSessionHandler(save_dir=tmp_path)
    store = SqliteStore.open_in_memory()
    sd = _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="mawdeep",
        player_name="Rux",
        player_id="player-1",
        snapshot=_fresh_snapshot(),
        store=store,
        genre_pack=MagicMock(),
        orchestrator=MagicMock(run_narration_turn=AsyncMock()),
    )
    handler._session_data = sd
    handler._room = None  # explicit: legacy non-slug path

    await handler.cleanup()

    # The store WAS closed — non-slug path owns its own store. Operating
    # on it now should raise.
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        store.save(_fresh_snapshot())


# ---------------------------------------------------------------------------
# replace_snapshot — chargen materialization swap (ADR-037 Python port)
# Regression: playtest 2026-04-26 mawdeep-mp [BUG] MP chargen — second
# player joining replaces first player's character/party state on
# Player 1's client. Root cause was sd.snapshot diverging from the
# room's _snapshot during materialize_from_genre_pack.
# ---------------------------------------------------------------------------


def test_replace_snapshot_swaps_canonical_reference():
    """After replace_snapshot, room.snapshot returns the new object.

    Without this primitive, the per-session ``sd.snapshot`` reference
    diverges from ``room._snapshot`` at chargen-materialization time and
    ``room.save()`` persists the pre-materialization (empty) snapshot.
    """
    room = SessionRoom(slug="2026-04-26-mawdeep-mp", mode=GameMode.MULTIPLAYER)
    snap1 = _fresh_snapshot()
    store = MagicMock()
    room.bind_world(snapshot=snap1, store=store)

    snap2 = _fresh_snapshot()
    snap2.location = "MaterializedEntrance"
    room.replace_snapshot(snap2)

    assert room.snapshot is snap2
    # Save now persists the new snapshot, not the original.
    room.save()
    assert store.save.call_count == 1
    assert store.save.call_args.args[0] is snap2


def test_replace_snapshot_raises_before_bind():
    """No silent fallback — calling replace_snapshot pre-bind is a
    contract violation that must surface as a loud RuntimeError so the
    GM panel sees the wiring bug instead of a corrupted persist later.
    """
    room = SessionRoom(slug="slug", mode=GameMode.MULTIPLAYER)
    with pytest.raises(RuntimeError, match="bind_world"):
        room.replace_snapshot(_fresh_snapshot())


# ---------------------------------------------------------------------------
# MP second-player chargen-commit regression
# ---------------------------------------------------------------------------


_MP_SLUG = "2026-04-26-mawdeep-mp"


def _content_packs_path():
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[3]
        / "sidequest-content"
        / "genre_packs"
    )


def _seed_mp_game(save_dir, slug: str) -> None:
    from sidequest.game.persistence import (
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
        mode=GameMode.MULTIPLAYER,
        genre_slug="caverns_and_claudes",
        world_slug="grimvault",
    )
    store.close()


def _make_mp_handler(save_dir, socket_id: str, registry):
    import asyncio as _asyncio

    from sidequest.server.session_handler import WebSocketSessionHandler

    handler = WebSocketSessionHandler(
        save_dir=save_dir,
        genre_pack_search_paths=[_content_packs_path()],
    )
    handler.attach_room_context(
        registry=registry,
        socket_id=socket_id,
        out_queue=_asyncio.Queue(),
    )
    return handler


async def _slug_connect(handler, *, player_id: str, player_name: str, slug: str):
    from sidequest.protocol.messages import (
        SessionEventMessage,
        SessionEventPayload,
    )

    return await handler.handle_message(
        SessionEventMessage(
            type="SESSION_EVENT",
            player_id=player_id,
            payload=SessionEventPayload(
                event="connect",
                game_slug=slug,
                player_name=player_name,
            ),
        )
    )


async def _walk_chargen_to_complete(handler, *, player_id: str):
    """Drive the slug-connected handler through chargen scenes and
    confirm. Mirrors the helper in test_chargen_persist_and_play but
    parameterized on player_id so a second player commit can be driven
    on a different handler bound to the same room.
    """
    from sidequest.protocol.messages import (
        CharacterCreationMessage,
        CharacterCreationPayload,
        ErrorMessage,
    )

    sd = handler._session_data
    builder = sd.builder
    assert builder is not None, "builder must exist after slug-connect for chargen"

    while not builder.is_confirmation():
        scene = builder.current_scene()
        if scene.choices:
            payload = CharacterCreationPayload(phase="scene", choice="1")
        elif scene.allows_freeform:
            payload = CharacterCreationPayload(phase="scene", choice=player_id)
        else:
            payload = CharacterCreationPayload(phase="continue")
        out = await handler.handle_message(
            CharacterCreationMessage(payload=payload, player_id=player_id)
        )
        if out and isinstance(out[0], ErrorMessage):
            raise AssertionError(f"walk error: {out[0].payload.message}")

    return await handler.handle_message(
        CharacterCreationMessage(
            payload=CharacterCreationPayload(phase="confirmation"),
            player_id=player_id,
        )
    )


def _content_pack_present() -> bool:
    return (_content_packs_path() / "caverns_and_claudes").is_dir()


@pytest.mark.asyncio
async def test_mp_second_player_chargen_appends_seat(tmp_path):
    """Regression for playtest 2026-04-26 mawdeep-mp [BUG] MP chargen:
    second player joining replaces first player's character/party state.

    Pre-fix: P2's chargen-commit took the first-commit branch because
    ``sd.store.load()`` returned an empty snapshot (P1's room/sd
    snapshot divergence persisted nothing). materialize_from_genre_pack
    produced a fresh snapshot whose ``player_seats`` was empty, then
    P2's seat-bind upsert ran on the empty dict — seat_count went 1→1
    instead of 1→2, evicting P1.

    Post-fix: room.replace_snapshot keeps room/sd snapshots aligned,
    so P2's commit observes the existing characters, takes the
    second-commit branch, and appends to the existing player_seats.
    """
    if not _content_pack_present():
        pytest.skip("content pack caverns_and_claudes not found")

    from sidequest.server.session_room import RoomRegistry

    _seed_mp_game(tmp_path, _MP_SLUG)
    registry = RoomRegistry()

    # Player 1: connect, walk chargen, commit.
    h1 = _make_mp_handler(tmp_path, "sock-p1", registry)
    await _slug_connect(h1, player_id="Fonzie", player_name="Fonzie", slug=_MP_SLUG)
    await _walk_chargen_to_complete(h1, player_id="Fonzie")

    room = registry.get(_MP_SLUG)
    assert room is not None
    assert room.snapshot is not None
    # P1's commit must have populated seats with exactly Fonzie.
    assert dict(room.snapshot.player_seats) == {"Fonzie": "Fonzie"}, (
        f"After P1 commit, seats must contain Fonzie only; got "
        f"{dict(room.snapshot.player_seats)}"
    )

    # Player 2: connect to same slug, walk chargen, commit.
    h2 = _make_mp_handler(tmp_path, "sock-p2", registry)
    await _slug_connect(h2, player_id="Richie", player_name="Richie", slug=_MP_SLUG)
    await _walk_chargen_to_complete(h2, player_id="Richie")

    # The lie detector: both seats survive on the canonical room snapshot.
    seats = dict(room.snapshot.player_seats)
    assert "Fonzie" in seats, (
        f"P1 (Fonzie) must remain seated after P2's chargen-commit; got {seats}"
    )
    assert "Richie" in seats, (
        f"P2 (Richie) must be appended to seats; got {seats}"
    )
    assert len(seats) == 2, (
        f"seat_count must go 1→2 across MP commits, not 1→1; got {seats}"
    )


@pytest.mark.asyncio
async def test_mp_second_player_chargen_preserves_npc_registry(tmp_path):
    """Regression for playtest 2026-04-26 mawdeep-mp [BUG] MP chargen:
    npc_registry was wiped on P2's chargen-commit because the first-
    commit branch (which clears registry) ran instead of the second-
    commit branch (which preserves it).

    Seed an NPC into the room snapshot after P1's commit, then walk
    P2 through chargen and assert the NPC survives the commit.
    """
    if not _content_pack_present():
        pytest.skip("content pack caverns_and_claudes not found")

    from sidequest.game.session import NpcRegistryEntry
    from sidequest.server.session_room import RoomRegistry

    _seed_mp_game(tmp_path, _MP_SLUG)
    registry = RoomRegistry()

    h1 = _make_mp_handler(tmp_path, "sock-p1", registry)
    await _slug_connect(h1, player_id="Fonzie", player_name="Fonzie", slug=_MP_SLUG)
    await _walk_chargen_to_complete(h1, player_id="Fonzie")

    room = registry.get(_MP_SLUG)
    assert room is not None and room.snapshot is not None

    # Seed an NPC P1 has "encountered" in the shared room snapshot.
    room.snapshot.npc_registry.append(
        NpcRegistryEntry(name="Mawdeep Innkeeper", last_seen_turn=1)
    )
    pre_len = len(room.snapshot.npc_registry)
    assert pre_len == 1

    # P2 walks chargen and commits. Pre-fix this would clear the registry.
    h2 = _make_mp_handler(tmp_path, "sock-p2", registry)
    await _slug_connect(h2, player_id="Richie", player_name="Richie", slug=_MP_SLUG)
    await _walk_chargen_to_complete(h2, player_id="Richie")

    post_len = len(room.snapshot.npc_registry)
    assert post_len == pre_len, (
        f"npc_registry must be preserved across the second player's "
        f"chargen-commit; pre={pre_len} post={post_len} entries="
        f"{[e.name for e in room.snapshot.npc_registry]}"
    )
    assert any(
        e.name == "Mawdeep Innkeeper" for e in room.snapshot.npc_registry
    ), "Seeded NPC must survive P2 chargen-commit"
