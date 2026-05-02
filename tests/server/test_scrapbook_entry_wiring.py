"""Wiring test for SCRAPBOOK_ENTRY emission (pingpong 2026-04-26 [S3-REGRESSION]).

The UI's ImageBusProvider has been wired to consume SCRAPBOOK_ENTRY for two
stories, but the server never emitted any. This test drives a real turn
through the orchestrator and asserts:

1. A row lands in the ``scrapbook_entries`` table.
2. A SCRAPBOOK_ENTRY event is appended to the journal.
3. A reconnecting client receives the prior SCRAPBOOK_ENTRY frame during
   replay (closes the loop on the gallery use-case).

Mocked at ``Orchestrator.run_narration_turn`` exactly like
``test_event_log_wiring.py`` — same fake-narration pattern, same in-memory
DB. No FastAPI, no daemon, no LLM calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.event_log import EventLog
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.session import GameSnapshot
from sidequest.protocol import GameMessage
from sidequest.protocol.enums import MessageType
from sidequest.server.session_handler import WebSocketSessionHandler
from sidequest.server.session_room import RoomRegistry

_GENRE = "test_genre"
_WORLD = "flickering_reach"
_SLUG = "scrapbook-wiring-fixture"
_SLUG_RESUME = "scrapbook-resume-fixture"
_FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "packs"


def _seed_with_character(tmp_path: Path, slug: str) -> None:
    db = db_path_for_slug(tmp_path, slug)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=slug,
        mode=GameMode.SOLO,
        genre_slug=_GENRE,
        world_slug=_WORLD,
    )
    core = CreatureCore(
        name="Thorn",
        description="A wandering fighter",
        personality="Grim",
        inventory=Inventory(),
    )
    char = Character(
        core=core,
        char_class="Fighter",
        race="Human",
        backstory="A wanderer.",
    )
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.characters = [char]
    store.init_session(_GENRE, _WORLD)
    store.save(snap)
    store.close()


def _fake_narration_result():
    """Build a narration result with the structured fields the scrapbook
    emitter reuses: location, npcs_present, footnotes."""
    from sidequest.agents.orchestrator import (
        NarrationTurnResult,
        NpcMention,
        VisualScene,
    )

    return NarrationTurnResult(
        narration=(
            "The dungeon echoes with your footsteps. A lantern flickers near the rough-hewn altar."
        ),
        location="Forgotten Crypt",
        visual_scene=VisualScene.from_dict(
            {
                "subject": "lantern-lit altar in a crypt",
                "tier": "scene_illustration",
                "mood": "ominous",
                "tags": ["crypt", "altar"],
            }
        ),
        npcs_present=[
            NpcMention(name="Caretaker Eldrin", role="silent witness", side="neutral"),
        ],
        footnotes=[
            {"summary": "The altar bears claw marks far too large for any human."},
        ],
        is_degraded=False,
        agent_duration_ms=42,
    )


@pytest.mark.asyncio
async def test_scrapbook_entry_persists_and_journals(tmp_path: Path) -> None:
    """Drive one PLAYER_ACTION → NARRATION turn → assert the scrapbook
    side-effects landed: row in ``scrapbook_entries`` AND a SCRAPBOOK_ENTRY
    row in ``events``."""
    _seed_with_character(tmp_path, _SLUG)
    handler = WebSocketSessionHandler(
        save_dir=tmp_path,
        genre_pack_search_paths=[_FIXTURE_PACKS],
    )
    queue: asyncio.Queue[object] = asyncio.Queue()
    handler.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-alice",
        out_queue=queue,
    )

    connect = GameMessage.model_validate(
        {
            "type": "SESSION_EVENT",
            "player_id": "alice",
            "payload": {
                "event": "connect",
                "game_slug": _SLUG,
                "last_seen_seq": 0,
            },
        }
    )

    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=_fake_narration_result()),
    ):
        await handler.handle_message(connect)

        action = GameMessage.model_validate(
            {
                "type": "PLAYER_ACTION",
                "player_id": "alice",
                "payload": {"action": "I look around the dungeon."},
            }
        )
        await handler.handle_message(action)

    db = db_path_for_slug(tmp_path, _SLUG)
    store = SqliteStore(db)
    store.initialize()
    try:
        # 1. scrapbook_entries row landed.
        rows = store._conn.execute(
            "SELECT turn_id, location, narrative_excerpt, scene_title, scene_type "
            "FROM scrapbook_entries"
        ).fetchall()
        assert rows, "expected at least one row in scrapbook_entries"
        turn_id, location, excerpt, scene_title, scene_type = rows[0]
        assert isinstance(turn_id, int)
        assert location, "scrapbook entry missing location"
        assert "dungeon" in excerpt.lower() or "altar" in excerpt.lower(), (
            f"excerpt did not echo narrator prose: {excerpt!r}"
        )
        assert scene_type == "scene_illustration"
        assert scene_title and "altar" in scene_title.lower()

        # 2. SCRAPBOOK_ENTRY row in events journal.
        events = EventLog(store).read_since(since_seq=0)
        kinds = [e.kind for e in events]
        assert "SCRAPBOOK_ENTRY" in kinds, f"expected SCRAPBOOK_ENTRY in event journal; got {kinds}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reconnecting_client_replays_prior_scrapbook_entry(
    tmp_path: Path,
) -> None:
    """The full loop: emit during turn 1 (handler A), then connect a fresh
    handler B against the same save and assert B receives a SCRAPBOOK_ENTRY
    frame in its connect outbound. Catches "row exists but replay drops it"
    failures."""
    _seed_with_character(tmp_path, _SLUG_RESUME)

    # ------- Handler A: drive a turn that emits a SCRAPBOOK_ENTRY -------
    handler_a = WebSocketSessionHandler(
        save_dir=tmp_path,
        genre_pack_search_paths=[_FIXTURE_PACKS],
    )
    queue_a: asyncio.Queue[object] = asyncio.Queue()
    handler_a.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-alice-a",
        out_queue=queue_a,
    )

    connect = GameMessage.model_validate(
        {
            "type": "SESSION_EVENT",
            "player_id": "alice",
            "payload": {
                "event": "connect",
                "game_slug": _SLUG_RESUME,
                "last_seen_seq": 0,
            },
        }
    )
    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=_fake_narration_result()),
    ):
        await handler_a.handle_message(connect)
        action = GameMessage.model_validate(
            {
                "type": "PLAYER_ACTION",
                "player_id": "alice",
                "payload": {"action": "I look around the dungeon."},
            }
        )
        await handler_a.handle_message(action)

    # ------- Handler B: fresh reconnect, last_seen_seq=0 (full replay) -------
    handler_b = WebSocketSessionHandler(
        save_dir=tmp_path,
        genre_pack_search_paths=[_FIXTURE_PACKS],
    )
    queue_b: asyncio.Queue[object] = asyncio.Queue()
    handler_b.attach_room_context(
        registry=RoomRegistry(),
        socket_id="sock-alice-b",
        out_queue=queue_b,
    )
    outbound_b = await handler_b.handle_message(connect)

    types_b = [getattr(m, "type", None) for m in outbound_b]
    assert MessageType.SCRAPBOOK_ENTRY in types_b, (
        f"reconnecting client must replay SCRAPBOOK_ENTRY; got {types_b}"
    )
    # The replayed entry must carry the metadata from handler A's turn.
    scrapbook_msgs = [
        m for m in outbound_b if getattr(m, "type", None) == MessageType.SCRAPBOOK_ENTRY
    ]
    payload = scrapbook_msgs[0].payload
    assert getattr(payload, "location", "") != ""
    assert getattr(payload, "narrative_excerpt", "")
