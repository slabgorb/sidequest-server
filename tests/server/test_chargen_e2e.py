"""End-to-end WebSocket integration for chargen — Slice E (Story 2.2).

Exercises the full dispatch path through the real FastAPI WebSocket layer:
no mocked genre pack, no mocked session handler. Loads real genre packs
from ``sidequest-content`` and walks chargen from ``connect`` through
``confirmation`` to the ``complete`` message.

This is the 2.2 acceptance canary — if this passes for two genres (one
display-only + freeform-name flow, one choice-heavy flow), the chargen
dispatch is wired end-to-end and the UI can drive it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sidequest.server.app import create_app


CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


from tests.server.conftest import mock_claude_client_factory as _mock_claude_client_factory  # noqa: E402


def _make_client(tmp_path: Path) -> TestClient:
    if not CONTENT_ROOT.is_dir():
        pytest.skip(f"content root not found at {CONTENT_ROOT}")
    app = create_app(
        claude_client_factory=_mock_claude_client_factory(),
        genre_pack_search_paths=[CONTENT_ROOT],
        save_dir=tmp_path,
    )
    return TestClient(app)


def _connect_payload(genre: str, world: str, player_name: str = "Rux") -> dict:
    return {
        "type": "SESSION_EVENT",
        "payload": {
            "event": "connect",
            "player_name": player_name,
            "genre": genre,
            "world": world,
        },
        "player_id": "",
    }


def _chargen_payload(**kwargs: object) -> dict:
    payload: dict[str, object] = {"phase": "scene"}
    payload.update(kwargs)
    return {
        "type": "CHARACTER_CREATION",
        "payload": payload,
        "player_id": "",
    }


def _recv(ws) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(ws.receive_text())


# ---------------------------------------------------------------------------
# caverns_and_claudes — display-only scenes + one choice scene + freeform
# ---------------------------------------------------------------------------


class TestCavernsAndClaudesFlow:
    """caverns_and_claudes has four scenes:
      0. the_roll — display-only (phase=continue), rolls 3d6 strict
      1. pronouns — choice + allows_freeform, pronoun_hint
      2. the_kit — display-only (phase=continue), equipment_generation
      3. the_mouth — display-only (phase=continue)

    Full flow: connect → continue → scene{choice=1} → continue → continue →
    confirmation → complete.
    """

    def test_full_flow_reaches_complete(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
            pytest.skip("caverns_and_claudes content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            # ---- Connect ---------------------------------------------------
            ws.send_json(_connect_payload("caverns_and_claudes", "flickering_reach"))
            connected = _recv(ws)
            assert connected["type"] == "SESSION_EVENT"
            assert connected["payload"]["event"] == "connected"
            assert connected["payload"]["has_character"] is False

            # ---- Scene 0 (the_roll): display-only, phase=continue ---------
            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "continue"},
                "player_id": "",
            })
            msg = _recv(ws)
            assert msg["type"] == "CHARACTER_CREATION"
            assert msg["payload"]["phase"] == "scene"
            # After the_roll auto-advances we should be on the pronouns scene
            # — choices present, allows_freeform true.
            assert msg["payload"]["scene_index"] == 1
            assert msg["payload"]["choices"]
            assert len(msg["payload"]["choices"]) == 3
            assert msg["payload"]["allows_freeform"] is True
            # the_roll declared stat_generation; rolled stats stay on the
            # wire only for scenes that declared the roll — pronouns did
            # not. ProtocolBase strips None-valued fields at serialization,
            # so the field is absent rather than explicitly null.
            assert "rolled_stats" not in msg["payload"]

            # ---- Scene 1 (pronouns): pick the first choice (she/her) ------
            ws.send_json(_chargen_payload(choice="1"))
            msg = _recv(ws)
            assert msg["payload"]["phase"] == "scene"
            assert msg["payload"]["scene_index"] == 2  # the_kit

            # ---- Scene 2 (the_kit): display-only -------------------------
            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "continue"},
                "player_id": "",
            })
            msg = _recv(ws)
            assert msg["payload"]["phase"] == "scene"
            assert msg["payload"]["scene_index"] == 3  # the_mouth

            # ---- Scene 3 (the_mouth): display-only, advances to Confirmation
            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "continue"},
                "player_id": "",
            })
            summary_msg = _recv(ws)
            assert summary_msg["type"] == "CHARACTER_CREATION"
            assert summary_msg["payload"]["phase"] == "confirmation"
            summary = summary_msg["payload"]["summary"]
            assert summary is not None
            # Lobby-name fallback because caverns has no name-entry scene.
            assert "Name: Rux" in summary
            # Pronouns from scene 1 pick.
            assert "Pronouns: she/her" in summary
            # Rolled stats present (3d6 strict fired at construction).
            assert "Stats: STR " in summary
            # Default class from rules.yaml (caverns has default_class: Delver).
            assert "Delver" in summary

            # ---- Confirmation commit → complete ---------------------------
            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "confirmation"},
                "player_id": "",
            })
            complete = _recv(ws)
            assert complete["type"] == "CHARACTER_CREATION"
            assert complete["payload"]["phase"] == "complete"
            assert complete["payload"]["character"] is not None
            character = complete["payload"]["character"]
            # Character name is the lobby-provided name (caverns has no
            # name-entry scene; fallback chain is scene > lobby > "Player").
            assert character["core"]["name"] == "Rux"
            assert character["char_class"] == "Delver"


# ---------------------------------------------------------------------------
# elemental_harmony — choice-heavy scenes
# ---------------------------------------------------------------------------


class TestElementalHarmonyFlow:
    """elemental_harmony opens on a choice scene (origins), making it the
    right fixture for exercising the phase=scene + choice="1" path through
    the full pipeline.
    """

    def test_connect_then_first_choice_advances(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_payload("elemental_harmony", "burning_peace"))
            connected = _recv(ws)
            assert connected["payload"]["event"] == "connected"
            assert connected["payload"]["has_character"] is False

            # scene 0 has choices — send choice=1
            ws.send_json(_chargen_payload(choice="1"))
            msg = _recv(ws)
            assert msg["type"] == "CHARACTER_CREATION"
            # Either next scene or confirmation — both legitimate depending on
            # whether scene 0 has a hook_prompt (which would route to
            # AwaitingFollowup).
            assert msg["payload"]["phase"] in ("scene", "confirmation")

    def test_invalid_choice_returns_error_does_not_disconnect(
        self, tmp_path: Path
    ) -> None:
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_payload("elemental_harmony", "burning_peace"))
            _recv(ws)  # connected

            # Out-of-range numeric index
            ws.send_json(_chargen_payload(choice="999"))
            err = _recv(ws)
            assert err["type"] == "ERROR"
            assert "Invalid choice" in err["payload"]["message"]

            # Connection is still live — recoverable error, not a disconnect.
            ws.send_json(_chargen_payload(choice="1"))
            ok = _recv(ws)
            assert ok["type"] == "CHARACTER_CREATION"


# ---------------------------------------------------------------------------
# Navigation — back through the WebSocket layer
# ---------------------------------------------------------------------------


class TestBackActionFlow:
    """The 2.2 AC calls out ``go_back`` through a scene with side-effects
    correctly reverting accumulated state. This exercises that path
    end-to-end through the WebSocket rather than unit-testing it against
    the handler in isolation.
    """

    def test_back_reverts_to_previous_scene(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_payload("elemental_harmony", "burning_peace"))
            _recv(ws)  # connected

            # Advance one scene.
            ws.send_json(_chargen_payload(choice="1"))
            after_choice = _recv(ws)
            if after_choice["payload"]["phase"] != "scene":
                pytest.skip(
                    "elemental_harmony scene 0 transitions directly to a "
                    "non-scene phase — no scene seat to go back to"
                )
            scene_after = after_choice["payload"]["scene_index"]

            # Go back.
            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "scene", "action": "back"},
                "player_id": "",
            })
            back = _recv(ws)
            assert back["type"] == "CHARACTER_CREATION"
            assert back["payload"]["phase"] == "scene"
            if scene_after is not None and back["payload"]["scene_index"] is not None:
                assert back["payload"]["scene_index"] < scene_after


# ---------------------------------------------------------------------------
# Structured error contract
# ---------------------------------------------------------------------------


class TestStructuredErrors:
    """2.2 AC: "Invalid inputs produce structured error messages, never
    exceptions through the WebSocket." These tests exercise error paths
    through the full pipeline and assert the connection remains usable.
    """

    def test_chargen_before_connect_returns_error(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(_chargen_payload(choice="1"))
            err = _recv(ws)
            assert err["type"] == "ERROR"
            assert "AwaitingConnect" in err["payload"]["message"]

    def test_unknown_action_returns_error(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
            pytest.skip("caverns_and_claudes content not found")
        client = _make_client(tmp_path)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_payload("caverns_and_claudes", "flickering_reach"))
            _recv(ws)

            ws.send_json({
                "type": "CHARACTER_CREATION",
                "payload": {"phase": "scene", "action": "bogus"},
                "player_id": "",
            })
            err = _recv(ws)
            assert err["type"] == "ERROR"
            assert "Unknown chargen action" in err["payload"]["message"]
