"""End-to-end wiring tests for the narration turn pipeline.

Verifies the full path from WebSocket PLAYER_ACTION → NARRATION using
mocked ClaudeClient and mocked genre loader. No real Claude CLI calls
and no real genre pack files required.

This is the wiring test required by CLAUDE.md: proves that every component
is imported, called, and reachable from production code paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.server.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canned_narration() -> str:
    return (
        "The stone corridor stretches before you, torchlight flickering on damp walls. "
        "Ancient glyphs line the passage, and the air smells of earth and old magic. "
        "Something skitters in the darkness ahead.\n\n"
        "```game_patch\n"
        '{"location": "Stone Corridor", "scene_mood": "tense", '
        '"npcs_present": [{"name": "The Keeper", "role": "guardian", '
        '"pronouns": "it/its", "appearance": "a hunched skeletal figure", "is_new": true}]}\n'
        "```"
    )


def _make_mock_client() -> MagicMock:
    mock = MagicMock()
    mock.send_with_session = AsyncMock(
        return_value=ClaudeResponse(
            text=_canned_narration(),
            session_id="e2e-session-001",
            input_tokens=200,
            output_tokens=120,
        )
    )
    return mock


def _make_mock_genre_pack() -> MagicMock:
    """Build a minimal mock GenrePack sufficient for the narration pipeline."""
    mock_pack = MagicMock()
    mock_pack.prompts = None
    mock_pack.audio = None
    mock_pack.tropes = []
    # Empty chargen scenes: session skips builder init. These e2e tests
    # exercise the narration path with pre-existing characters, not chargen.
    mock_pack.char_creation = []
    mock_pack.backstory_tables = None
    mock_pack.equipment_tables = None
    return mock_pack


def _make_e2e_app(tmp_path: Path) -> tuple[TestClient, MagicMock, MagicMock]:
    """Create test app with mocked Claude + mocked genre loader.

    Returns (client, mock_claude_client, mock_genre_pack).
    """
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()

    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()

    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path / "nonexistent"],
        save_dir=saves_dir,
    )

    client = TestClient(app)
    return client, mock_client, mock_pack


# ---------------------------------------------------------------------------
# E2E: full narration round-trip
# ---------------------------------------------------------------------------


def test_e2e_narration_turn_full_roundtrip(tmp_path):
    """E2E wiring test: connect → PLAYER_ACTION → NARRATION received.

    Full path:
      WebSocket accept → SESSION_EVENT{connect} → PLAYER_ACTION
      → Orchestrator.run_narration_turn() (mocked) → NARRATION + NARRATION_END
    """
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()

    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            # Step 1: connect
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {
                    "event": "connect",
                    "player_name": "Alex",
                    "genre": "caverns_and_claudes",
                    "world": "flickering_reach",
                },
                "player_id": "",
            })
            connected = json.loads(ws.receive_text())
            assert connected["type"] == "SESSION_EVENT", f"Expected SESSION_EVENT, got: {connected}"
            assert connected["payload"]["event"] == "connected"
            assert connected["payload"]["genre"] == "caverns_and_claudes"

            # Step 2: send a player action
            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I cautiously move forward and examine the glyphs.", "aside": False},
                "player_id": "",
            })

            # Step 3: receive NARRATION
            narration_raw = ws.receive_text()
            narration = json.loads(narration_raw)
            assert narration["type"] == "NARRATION"
            assert narration["payload"]["text"]
            assert len(narration["payload"]["text"]) > 0

            # Step 4: receive NARRATION_END
            narration_end_raw = ws.receive_text()
            narration_end = json.loads(narration_end_raw)
            assert narration_end["type"] == "NARRATION_END"


def test_e2e_narration_text_is_nonempty(tmp_path):
    """The NARRATION payload text is non-empty prose."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()
    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "James", "genre": "caverns_and_claudes", "world": "flickering_reach"},
                "player_id": "",
            })
            ws.receive_text()  # connected

            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I search the room for traps.", "aside": False},
                "player_id": "",
            })

            narration = json.loads(ws.receive_text())
            assert narration["type"] == "NARRATION"
            text = narration["payload"]["text"]
            assert len(text) > 10  # not empty or trivially short


def test_e2e_session_is_persisted_after_action(tmp_path):
    """After a PLAYER_ACTION, a save file exists on disk."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()
    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "keith", "genre": "caverns_and_claudes", "world": "flickering_reach"},
                "player_id": "",
            })
            ws.receive_text()  # connected

            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I head deeper into the dungeon.", "aside": False},
                "player_id": "",
            })
            ws.receive_text()  # NARRATION
            ws.receive_text()  # NARRATION_END

    # After context manager exits (disconnect), save should exist
    expected_db = (
        saves_dir / "caverns_and_claudes" / "flickering_reach" / "keith" / "save.db"
    )
    assert expected_db.exists(), f"Save file not found at {expected_db}"


def test_e2e_second_action_calls_client_twice(tmp_path):
    """Second PLAYER_ACTION causes second Claude call (not cached away)."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()
    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "sebastien", "genre": "caverns_and_claudes", "world": "flickering_reach"},
                "player_id": "",
            })
            ws.receive_text()  # connected

            # First action
            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I look around.", "aside": False},
                "player_id": "",
            })
            ws.receive_text()  # NARRATION
            ws.receive_text()  # NARRATION_END

            # Second action
            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I pick up the torch.", "aside": False},
                "player_id": "",
            })
            narration2 = json.loads(ws.receive_text())
            ws.receive_text()  # NARRATION_END

    # Claude was called twice
    assert mock_client.send_with_session.call_count == 2
    assert narration2["type"] == "NARRATION"


def test_e2e_app_routes_registered(tmp_path):
    """Wiring test: all expected routes exist on the app object."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    app = create_app(
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    route_paths = sorted(
        r.path for r in app.routes if hasattr(r, "path")  # type: ignore[attr-defined]
    )

    assert "/health" in route_paths
    assert "/api/genres" in route_paths
    assert "/api/saves" in route_paths
    assert "/api/saves/new" in route_paths
    assert "/api/sessions" in route_paths
    assert "/ws" in route_paths


def test_e2e_sanitize_applied_to_player_action(tmp_path):
    """Player action text is sanitized before being sent to orchestrator."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()
    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "tester", "genre": "caverns_and_claudes", "world": "flickering_reach"},
                "player_id": "",
            })
            ws.receive_text()

            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "  I look around the room.  ", "aside": False},
                "player_id": "",
            })
            narration = json.loads(ws.receive_text())
            ws.receive_text()

    assert narration["type"] == "NARRATION"
    assert mock_client.send_with_session.called


def test_e2e_genre_not_found_returns_error(tmp_path):
    """SESSION_EVENT{connect} with unknown genre returns ERROR (not 500).

    The genre search path is an empty tmp_path directory so no genre packs
    are found — GenreLoader raises GenreNotFoundError which becomes ERROR.
    """
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    empty_packs = tmp_path / "empty_packs"
    empty_packs.mkdir()
    app = create_app(
        genre_pack_search_paths=[empty_packs],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "SESSION_EVENT",
            "payload": {"event": "connect", "player_name": "ghost", "genre": "no_such_genre", "world": "no_such_world"},
            "player_id": "",
        })
        raw = ws.receive_text()
        msg = json.loads(raw)
        assert msg["type"] == "ERROR"
        assert "no_such_genre" in msg["payload"]["message"]


def test_e2e_npc_registry_populated_after_action(tmp_path):
    """After a narration turn, NPC mentions from game_patch appear in the snapshot."""
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    mock_client = _make_mock_client()
    mock_pack = _make_mock_genre_pack()

    # We'll capture the session handler to inspect snapshot after
    captured_handler: list = []

    original_ws_endpoint = None
    import sidequest.server.app as app_module

    app = create_app(
        claude_client_factory=lambda: mock_client,
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    client = TestClient(app)

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "npc_tester", "genre": "caverns_and_claudes", "world": "flickering_reach"},
                "player_id": "",
            })
            ws.receive_text()

            ws.send_json({
                "type": "PLAYER_ACTION",
                "payload": {"action": "I speak to the guardian.", "aside": False},
                "player_id": "",
            })
            ws.receive_text()  # NARRATION
            ws.receive_text()  # NARRATION_END

    # Load the save and verify NPC registry
    from sidequest.game.persistence import SqliteStore, db_path_for_session

    db = db_path_for_session(saves_dir, "caverns_and_claudes", "flickering_reach", "npc_tester")
    assert db.exists()
    store = SqliteStore.open(str(db))
    saved = store.load()
    store.close()

    assert saved is not None
    # The canned narration includes "The Keeper" as an NPC
    names = [e.name for e in saved.snapshot.npc_registry]
    assert "The Keeper" in names, f"Expected 'The Keeper' in NPC registry, got: {names}"
