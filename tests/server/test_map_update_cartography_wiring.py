"""Wiring tests for MAP_UPDATE on cartography render — slice 1 of N.

Pingpong 2026-04-26 ``[S3-PORT-REGRESSION]``: ``MAP_UPDATE`` was declared in
the protocol layer but never constructed or emitted. UI side
(``MapOverlay``, ``Automapper``) is fully wired; the daemon's ``cartography``
tier is wired. The server just never sent the frame.

Slice 1 emits ``MAP_UPDATE`` alongside the cartography render dispatch
(twin-emission pattern, mirror of IMAGE/SCRAPBOOK_ENTRY at S3 fix). This
test suite proves:

1. **Unit:** ``build_map_update_payload`` produces a valid wire payload from
   a snapshot + cartography config (constructs cleanly, fields populated).
2. **Wiring:** firing a cartography render through ``_maybe_dispatch_render``
   pushes a ``MapUpdateMessage`` onto the player's outbound queue and emits
   the OTEL ``map.update_emitted`` watcher event.

Both tests stub the daemon (no socket round-trip) and the orchestrator
(no LLM calls) so they run in-process.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sidequest.agents.orchestrator import NarrationTurnResult, VisualScene
from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.session import GameSnapshot
from sidequest.protocol import GameMessage
from sidequest.protocol.enums import MessageType
from sidequest.protocol.messages import MapUpdateMessage, MapUpdatePayload
from sidequest.server.dispatch.map_update import (
    build_map_update_payload,
    cartography_metadata_from_config,
    explored_locations_from_snapshot,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry

_GENRE = "test_genre"
_WORLD = "flickering_reach"
_FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "packs"


# ---------------------------------------------------------------------------
# Unit: payload construction
# ---------------------------------------------------------------------------


def test_build_map_update_payload_constructs_cleanly_from_fixture_world() -> None:
    """``build_map_update_payload`` should turn a real fixture pack's
    cartography + a snapshot into a valid wire payload — no exceptions,
    all required fields populated, current location flagged."""
    from sidequest.genre.loader import load_genre_pack

    pack = load_genre_pack(_FIXTURE_PACKS / _GENRE)
    world = pack.worlds[_WORLD]

    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.location = "toods_dome"
    snap.current_region = "toods_dome"
    snap.discovered_regions = ["toods_dome", "blooming_tangle"]

    payload = build_map_update_payload(
        snapshot=snap, cartography=world.cartography,
    )

    assert payload is not None, "payload must construct from a real fixture"
    assert isinstance(payload, MapUpdatePayload)
    assert str(payload.current_location) == "toods_dome"
    assert str(payload.region) == "toods_dome"

    # Explored should include the discovered regions, and the current
    # location must be flagged so the UI's Automapper can highlight it.
    explored_ids = {loc.id for loc in payload.explored}
    assert "toods_dome" in explored_ids
    assert "blooming_tangle" in explored_ids
    current = [loc for loc in payload.explored if loc.is_current_room]
    assert len(current) == 1
    assert current[0].id == "toods_dome"

    # Cartography metadata must be present and carry the navigation mode +
    # at least the regions the snapshot referenced.
    assert payload.cartography is not None
    assert payload.cartography.navigation_mode == "region"
    assert payload.cartography.starting_region == "toods_dome"
    assert "toods_dome" in payload.cartography.regions
    # Routes should round-trip too (the fixture has many).
    assert payload.cartography.routes


def test_build_map_update_payload_returns_none_when_no_location() -> None:
    """A snapshot with no location must NOT produce a payload — the wire
    model requires non-blank current_location and emitting a fake placeholder
    would clear the UI's current-room highlight."""
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.location = ""
    payload = build_map_update_payload(snapshot=snap, cartography=None)
    assert payload is None


def test_explored_locations_includes_current_when_missing_from_discovered() -> None:
    """``explored_locations_from_snapshot`` must always surface the current
    location even if discovered_regions hasn't been populated yet (the
    common opening-turn case where the player has only just landed)."""
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.location = "toods_dome"
    snap.discovered_regions = []
    locs = explored_locations_from_snapshot(snap, cartography=None)
    assert any(loc.id == "toods_dome" and loc.is_current_room for loc in locs)


def test_cartography_metadata_returns_none_for_none() -> None:
    """Helper must short-circuit on missing cartography rather than raise."""
    assert cartography_metadata_from_config(None) is None


# ---------------------------------------------------------------------------
# Wiring: drive a cartography render through dispatch and assert MAP_UPDATE
# lands on the outbound queue + OTEL event fires.
# ---------------------------------------------------------------------------


def _seed_with_character(tmp_path: Path, slug: str) -> None:
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store, slug=slug, mode=GameMode.SOLO, genre_slug=_GENRE, world_slug=_WORLD,
    )
    core = CreatureCore(
        name="Mappa",
        description="A wandering cartographer",
        personality="Curious",
        inventory=Inventory(),
    )
    char = Character(
        core=core, char_class="Scholar", race="Human", backstory="Maps the world.",
    )
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.characters = [char]
    snap.location = "toods_dome"
    snap.current_region = "toods_dome"
    snap.discovered_regions = ["toods_dome"]
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()


def _fake_cartography_narration_result():
    """Build a narration result that triggers the cartography render path."""
    return NarrationTurnResult(
        narration=(
            "You unroll the parchment and survey the Reach: the steppe stretches "
            "north, the Tangle a green smear at the horizon."
        ),
        location="toods_dome",
        visual_scene=VisualScene.from_dict(
            {
                "subject": "weathered map of the Flickering Reach",
                "tier": "cartography",
                "mood": "contemplative",
                "tags": ["map", "overview"],
            }
        ),
        npcs_present=[],
        footnotes=[],
        is_degraded=False,
        agent_duration_ms=42,
    )


@pytest.mark.asyncio
async def test_cartography_render_emits_map_update_to_outbound_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end wiring: drive a turn whose visual_scene picks the
    ``cartography`` tier and assert (a) a ``MapUpdateMessage`` lands on the
    per-player outbound queue, (b) its payload carries the snapshot's
    location + the world's cartography metadata, and (c) the
    ``map.update_emitted`` OTEL watcher event fires.

    The daemon is stubbed via ``DaemonClient.is_available`` so no socket is
    contacted; the render flag is forced on.
    """
    slug = "map-update-wiring-fixture"
    _seed_with_character(tmp_path, slug)

    # Force the render feature flag on for this test.
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")

    handler = WebSocketSessionHandler(
        save_dir=tmp_path, genre_pack_search_paths=[_FIXTURE_PACKS],
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    handler.attach_room_context(
        registry=RoomRegistry(), socket_id="sock-mappa", out_queue=queue,
    )

    # Capture watcher events — they're published via watcher_hub.publish_event
    # inside session_handler. Patch the alias session_handler imports as
    # ``_watcher_publish`` so we see exactly what the production code emits.
    captured_events: list[tuple[str, dict]] = []

    def _capture(event_kind: str, fields: dict, **_kw: object) -> None:
        captured_events.append((event_kind, dict(fields)))

    monkeypatch.setattr(
        "sidequest.server.session_handler._watcher_publish", _capture,
    )

    # Stub the daemon so the render dispatch path believes the daemon is
    # reachable, but don't actually fire the background task at a real
    # socket. We only care that the MAP_UPDATE side-effect happens; the
    # IMAGE callback is irrelevant for this test.
    connect = GameMessage.model_validate(
        {
            "type": "SESSION_EVENT",
            "player_id": "mappa",
            "payload": {
                "event": "connect",
                "game_slug": slug,
                "last_seen_seq": 0,
            },
        }
    )

    # Override the autouse `_mock_daemon_client` guard with a stub that
    # claims availability — the conftest's `_UnavailableDaemonClient` would
    # short-circuit `_maybe_dispatch_render` before our cartography branch
    # runs. We still don't talk to a real socket: render() raises
    # CancelledError so the background task tears down cleanly.
    class _AvailableDaemonStub:
        socket_path = Path("/tmp/sq-test-cartography-stub.sock")

        def is_available(self) -> bool:
            return True

        async def render(self, params):  # noqa: ARG002
            raise asyncio.CancelledError()

        async def embed(self, text: str):  # noqa: ARG002
            raise RuntimeError("embed should not be called in this test")

    monkeypatch.setattr(
        "sidequest.server.session_handler.DaemonClient",
        lambda *a, **kw: _AvailableDaemonStub(),
    )

    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=_fake_cartography_narration_result()),
    ):
        await handler.handle_message(connect)
        # Drain the connect outbound so we only see post-action frames.
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        action = GameMessage.model_validate(
            {
                "type": "PLAYER_ACTION",
                "player_id": "mappa",
                "payload": {"action": "I unroll my map of the Reach."},
            }
        )
        await handler.handle_message(action)

    # Drain the queue and look for our MAP_UPDATE.
    drained: list[object] = []
    while not queue.empty():
        try:
            drained.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    map_msgs = [m for m in drained if isinstance(m, MapUpdateMessage)]
    assert map_msgs, (
        f"expected at least one MapUpdateMessage on the outbound queue; "
        f"saw {[type(m).__name__ for m in drained]}"
    )
    msg = map_msgs[0]
    assert msg.type == MessageType.MAP_UPDATE
    assert str(msg.payload.current_location) == "toods_dome"
    assert msg.payload.cartography is not None
    assert msg.payload.cartography.navigation_mode == "region"

    # OTEL: the lie-detector event must have fired.
    map_emit_events = [
        fields for kind, fields in captured_events
        if fields.get("field") == "map" and fields.get("op") == "update_emitted"
    ]
    assert map_emit_events, (
        "missing OTEL map.update_emitted event — without this the GM panel "
        "cannot verify the map subsystem is engaged (CLAUDE.md OTEL principle)"
    )
    emit = map_emit_events[0]
    assert emit["origin"] == "cartography_render"
    assert emit["tier"] == "cartography"
    assert emit["current_location"] == "toods_dome"
    assert emit["has_cartography"] is True


@pytest.mark.asyncio
async def test_non_cartography_render_does_not_emit_map_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative: a scene_illustration tier render must NOT trigger a
    MAP_UPDATE — the slice-1 trigger is gated on ``tier == "cartography"``.
    Catches accidental over-emission if the gate regresses."""
    slug = "map-update-negative-fixture"
    _seed_with_character(tmp_path, slug)
    monkeypatch.setenv("SIDEQUEST_RENDER_ENABLED", "1")

    handler = WebSocketSessionHandler(
        save_dir=tmp_path, genre_pack_search_paths=[_FIXTURE_PACKS],
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    handler.attach_room_context(
        registry=RoomRegistry(), socket_id="sock-mappa-neg", out_queue=queue,
    )

    scene_result = NarrationTurnResult(
        narration="A campfire crackles in the dome's shadow.",
        location="toods_dome",
        visual_scene=VisualScene.from_dict(
            {
                "subject": "campfire under stadium dome",
                "tier": "scene_illustration",
                "mood": "warm",
                "tags": [],
            }
        ),
        npcs_present=[],
        footnotes=[],
        is_degraded=False,
        agent_duration_ms=10,
    )

    connect = GameMessage.model_validate(
        {
            "type": "SESSION_EVENT",
            "player_id": "mappa",
            "payload": {
                "event": "connect",
                "game_slug": slug,
                "last_seen_seq": 0,
            },
        }
    )
    class _AvailableDaemonStub:
        socket_path = Path("/tmp/sq-test-cartography-stub.sock")

        def is_available(self) -> bool:
            return True

        async def render(self, params):  # noqa: ARG002
            raise asyncio.CancelledError()

        async def embed(self, text: str):  # noqa: ARG002
            raise RuntimeError("embed should not be called in this test")

    monkeypatch.setattr(
        "sidequest.server.session_handler.DaemonClient",
        lambda *a, **kw: _AvailableDaemonStub(),
    )

    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=scene_result),
    ):
        await handler.handle_message(connect)
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        action = GameMessage.model_validate(
            {
                "type": "PLAYER_ACTION",
                "player_id": "mappa",
                "payload": {"action": "I sit by the fire."},
            }
        )
        await handler.handle_message(action)

    drained: list[object] = []
    while not queue.empty():
        try:
            drained.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    map_msgs = [m for m in drained if isinstance(m, MapUpdateMessage)]
    assert not map_msgs, (
        f"scene_illustration must not trigger MAP_UPDATE; got {map_msgs}"
    )
