"""Integration: narrator outputs routed through EventLog + ProjectionFilter (MP-03 Task 3).

Verifies:
1. Alice connects via slug.
2. Alice sends PLAYER_ACTION.
3. The NARRATION response carries payload["seq"] >= 1.
4. EventLog has at least one NARRATION row after the turn.

Uses caverns_and_claudes / grimvault (same as other MP integration tests).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sidequest.game.event_log import EventLog
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS
from sidequest.server.app import create_app

_GENRE = "caverns_and_claudes"
_WORLD = "grimvault"
_SLUG = "2026-04-22-grimvault-event-log-wiring"


def _seed(tmp_path: Path, slug: str) -> None:
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
    store.close()


def _genre_packs_path() -> Path | None:
    return next(
        (p for p in DEFAULT_GENRE_PACK_SEARCH_PATHS if p.exists()),
        None,
    )


def _make_fake_narration_result() -> object:
    """Minimal NarrationTurnResult-like object for mocking the orchestrator."""
    from sidequest.agents.orchestrator import NarrationTurnResult

    return NarrationTurnResult(
        narration="The dungeon echoes with your footsteps.",
        location=None,
        quest_updates={},
        lore_established=[],
        npcs_present=[],
        is_degraded=False,
        agent_duration_ms=42,
    )


def test_narration_carries_seq_and_event_log_has_row(tmp_path: Path) -> None:
    """NARRATION response must carry seq >= 1 and EventLog must persist the row."""
    packs = _genre_packs_path()
    if packs is None:
        pytest.skip(f"No genre_packs directory found in {DEFAULT_GENRE_PACK_SEARCH_PATHS}")

    _seed(tmp_path, _SLUG)
    app = create_app(genre_pack_search_paths=[packs], save_dir=tmp_path)
    client = TestClient(app)

    fake_result = _make_fake_narration_result()

    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=fake_result),
    ), client.websocket_connect("/ws") as ws:
        # Connect via slug
        ws.send_json({
            "type": "SESSION_EVENT",
            "player_id": "alice",
            "payload": {
                "event": "connect",
                "game_slug": _SLUG,
                "last_seen_seq": 0,
            },
        })
        connected = ws.receive_json()
        assert connected["type"] == "SESSION_EVENT"
        assert connected["payload"]["event"] == "connected"

            # Skip chargen — send a PLAYER_ACTION (caverns_and_claudes has chargen
            # so we need to complete it first, but we can't in a unit test easily.
            # Instead use has_character=True by injecting a saved snapshot.
            # The mock narration result means the orchestrator won't actually be called.

    # Re-seed with a snapshot that has a character so we skip chargen
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory
    from sidequest.game.session import GameSnapshot

    db = db_path_for_slug(tmp_path, _SLUG)
    store = SqliteStore(db)
    store.initialize()
    # Build minimal character to mark has_character=True
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

    with patch(
        "sidequest.agents.orchestrator.Orchestrator.run_narration_turn",
        new=AsyncMock(return_value=fake_result),
    ), client.websocket_connect("/ws") as ws:
        # Connect (now has_character=True, skips chargen → Playing)
        ws.send_json({
            "type": "SESSION_EVENT",
            "player_id": "alice",
            "payload": {
                "event": "connect",
                "game_slug": _SLUG,
                "last_seen_seq": 0,
            },
        })
        connected = ws.receive_json()
        assert connected["type"] == "SESSION_EVENT"
        assert connected["payload"]["event"] == "connected"
        assert connected["payload"]["has_character"] is True

        # Send PLAYER_ACTION
        ws.send_json({
            "type": "PLAYER_ACTION",
            "player_id": "alice",
            "payload": {"action": "I look around the dungeon."},
        })

        # Drain until we see NARRATION
        narration_msg = None
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "NARRATION":
                narration_msg = m
                break

        assert narration_msg is not None, "Expected NARRATION message"
        # Core invariant: seq field present and >= 1
        assert "seq" in narration_msg["payload"], (
            f"NARRATION payload missing 'seq': {narration_msg['payload']}"
        )
        assert narration_msg["payload"]["seq"] >= 1, (
            f"Expected seq >= 1, got {narration_msg['payload']['seq']}"
        )

    # Confirm EventLog has at least one NARRATION row
    db = db_path_for_slug(tmp_path, _SLUG)
    store = SqliteStore(db)
    store.initialize()
    event_log = EventLog(store)
    rows = event_log.read_since(since_seq=0)
    narration_rows = [r for r in rows if r.kind == "NARRATION"]
    assert len(narration_rows) >= 1, (
        f"Expected at least one NARRATION row in EventLog, got {rows}"
    )
    store.close()
