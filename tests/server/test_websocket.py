"""Unit tests for sidequest.server.websocket.

Tests the WebSocket connection handling, protocol parsing, and error handling.
No real Claude CLI calls; genre loader is patched.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sidequest.server.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Create a mock ClaudeLike that returns canned narration."""
    from sidequest.agents.claude_client import ClaudeResponse

    mock = MagicMock()
    mock.send_with_session = AsyncMock(
        return_value=ClaudeResponse(
            text=(
                "The tavern door swings open. You step into the warm, smoky interior.\n\n"
                "```game_patch\n"
                '{"location": "The Rusty Flagon", "scene_mood": "cozy"}\n'
                "```"
            ),
            session_id="test-session-001",
            input_tokens=100,
            output_tokens=80,
        )
    )
    return mock


def _make_mock_genre_pack() -> MagicMock:
    mock = MagicMock()
    mock.prompts = None
    mock.audio = None
    mock.tropes = []
    # Empty chargen scenes: session skips builder init, which is the right
    # state for websocket-layer tests that don't care about chargen flow.
    mock.char_creation = []
    mock.backstory_tables = None
    mock.equipment_tables = None
    mock.inventory = None
    # Explicit zero-state for the opening-hook resolver (Story 2.3 Slice B):
    # MagicMock auto-attrs let ``pack.worlds.get(world)`` return a MagicMock
    # whose truthy ``.openings`` hangs ``random.choice`` in the resolver.
    mock.openings = []
    mock.worlds = {}
    return mock


def _make_app(tmp_path):
    """Create a test app with mocked client and patched genre loader scope."""
    mock_client = _make_mock_client()
    return create_app(
        claude_client_factory=lambda: mock_client,
        save_dir=tmp_path,
    )


def _connect_msg(genre: str = "test_genre", world: str = "test_world") -> dict:
    return {
        "type": "SESSION_EVENT",
        "payload": {
            "event": "connect",
            "player_name": "TestPlayer",
            "genre": genre,
            "world": world,
        },
        "player_id": "",
    }


# ---------------------------------------------------------------------------
# Connection acceptance
# ---------------------------------------------------------------------------


def test_websocket_health_endpoint(tmp_path):
    """Wiring test: /health is reachable after create_app."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_websocket_accepts_connection(tmp_path):
    """WebSocket /ws endpoint accepts a connection."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        assert ws is not None


# ---------------------------------------------------------------------------
# Malformed JSON handling
# ---------------------------------------------------------------------------


def test_websocket_malformed_json_returns_error(tmp_path):
    """Malformed JSON causes ERROR message. No silent fallback."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not valid json at all")
        raw = ws.receive_text()
        msg = json.loads(raw)
        assert msg["type"] == "ERROR"


# ---------------------------------------------------------------------------
# SESSION_EVENT{connect}
# ---------------------------------------------------------------------------


def test_websocket_connect_event_returns_connected(tmp_path):
    """SESSION_EVENT{connect} returns SESSION_EVENT{connected}."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    mock_pack = _make_mock_genre_pack()

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_msg("caverns_and_claudes", "flickering_reach"))
            raw = ws.receive_text()
            msg = json.loads(raw)
            assert msg["type"] == "SESSION_EVENT"
            assert msg["payload"]["event"] == "connected"
            assert msg["payload"]["genre"] == "caverns_and_claudes"
            assert msg["payload"]["world"] == "flickering_reach"


def test_websocket_connect_missing_genre_returns_error(tmp_path):
    """SESSION_EVENT{connect} without genre slug returns ERROR."""
    app = _make_app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "SESSION_EVENT",
            "payload": {"event": "connect", "player_name": "T", "genre": "", "world": "w"},
            "player_id": "",
        })
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ERROR"


def test_websocket_connect_missing_world_returns_error(tmp_path):
    """SESSION_EVENT{connect} without world slug returns ERROR."""
    app = _make_app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "SESSION_EVENT",
            "payload": {"event": "connect", "player_name": "T", "genre": "g", "world": ""},
            "player_id": "",
        })
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ERROR"


def test_websocket_connect_unknown_event_returns_error(tmp_path):
    """SESSION_EVENT with unknown event name returns ERROR."""
    app = _make_app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "SESSION_EVENT",
            "payload": {"event": "unknown_event"},
            "player_id": "",
        })
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ERROR"


# ---------------------------------------------------------------------------
# Unsupported message types
# ---------------------------------------------------------------------------


def test_unsupported_message_type_returns_error(tmp_path):
    """An unsupported message type returns an ERROR, not a crash."""
    app = _make_app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "SESSION_EVENT",
            "payload": {"event": "bogus_unsupported"},
            "player_id": "",
        })
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ERROR"


# ---------------------------------------------------------------------------
# PLAYER_ACTION before connect returns error
# ---------------------------------------------------------------------------


def test_player_action_before_connect_returns_error(tmp_path):
    """PLAYER_ACTION before SESSION_EVENT{connect} returns ERROR."""
    app = _make_app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "PLAYER_ACTION",
            "payload": {"action": "I look around the room", "aside": False},
            "player_id": "p1",
        })
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "ERROR"


# ---------------------------------------------------------------------------
# Connected response fields
# ---------------------------------------------------------------------------


def test_websocket_connect_has_character_field(tmp_path):
    """SESSION_EVENT{connected} includes has_character field."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    mock_pack = _make_mock_genre_pack()

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_msg())
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "SESSION_EVENT"
            assert "has_character" in msg["payload"]


def test_websocket_connect_player_name_echoed(tmp_path):
    """SESSION_EVENT{connected} echoes the player_name."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    mock_pack = _make_mock_genre_pack()

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "SESSION_EVENT",
                "payload": {"event": "connect", "player_name": "Bilbo", "genre": "g", "world": "w"},
                "player_id": "",
            })
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "SESSION_EVENT"
            assert msg["payload"]["player_name"] == "Bilbo"


def test_websocket_reconnect_accepted_in_playing_state(tmp_path):
    """A second SESSION_EVENT{connect} is accepted (reconnect scenario)."""
    app = _make_app(tmp_path)
    client = TestClient(app)
    mock_pack = _make_mock_genre_pack()

    with patch("sidequest.server.session_handler.GenreLoader") as MockLoader:
        MockLoader.return_value.load.return_value = mock_pack

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_msg("g1", "w1"))
            msg1 = json.loads(ws.receive_text())
            assert msg1["type"] == "SESSION_EVENT"

            # Reconnect with different world (genre switch)
            ws.send_json(_connect_msg("g2", "w2"))
            msg2 = json.loads(ws.receive_text())
            assert msg2["type"] == "SESSION_EVENT"
            assert msg2["payload"]["event"] == "connected"
