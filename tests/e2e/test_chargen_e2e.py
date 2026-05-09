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

import pytest
from fastapi.testclient import TestClient

from sidequest.server.app import create_app
from tests.server.conftest import (
    mock_claude_client_factory as _mock_claude_client_factory,
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path) -> TestClient:
    if not CONTENT_ROOT.is_dir():
        pytest.skip(f"content root not found at {CONTENT_ROOT}")
    app = create_app(
        claude_client_factory=_mock_claude_client_factory(),
        genre_pack_search_paths=[CONTENT_ROOT],
        save_dir=tmp_path,
    )
    return TestClient(app)


def _connect_payload(
    client: TestClient,
    genre: str,
    world: str,
    player_name: str = "Rux",
) -> dict:
    """Mint a game slug then build a slug-keyed connect envelope.

    Story 45-26: WS connect requires ``payload.game_slug``; the legacy
    ``(genre, world, player_name)`` connect path was deleted alongside
    the legacy ``/api/saves/*`` REST routes. Tests must mint a slug via
    ``POST /api/games`` and connect with ``game_slug``.
    """
    r = client.post(
        "/api/games",
        json={"genre_slug": genre, "world_slug": world, "mode": "solo"},
    )
    assert r.status_code == 201, f"Failed to mint slug: {r.text}"
    return {
        "type": "SESSION_EVENT",
        "payload": {
            "event": "connect",
            "player_name": player_name,
            "game_slug": r.json()["slug"],
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
    """caverns_and_claudes has six scenes (visible-dice era):
      0. the_roll — display-only, rolls a 3d6 pool (no labels)
      1. the_arrangement — assignment_required (arrange_assign × 6 + arrange_confirm)
      2. the_calling — class choice (Fighter/Mage/Cleric/Thief, filtered to qualifying)
      3. the_story — StoryInput (pronouns + freeform background/description)
      4. the_kit — display-only, equipment_generation: class_kit
      5. the_mouth — display-only

    Full flow: connect → continue → arrange_assign × 6 → arrange_confirm →
    scene{choice=1} → story_confirm → continue → continue → confirmation → complete.
    """

    def test_full_flow_reaches_complete(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
            pytest.skip("caverns_and_claudes content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            # ---- Connect ---------------------------------------------------
            ws.send_json(_connect_payload(client, "caverns_and_claudes", "flickering_reach"))
            connected = _recv(ws)
            assert connected["type"] == "SESSION_EVENT"
            assert connected["payload"]["event"] == "connected"
            assert connected["payload"]["has_character"] is False

            # ---- Initial chargen scene 0 (the_roll): display-only narration.
            initial = _recv(ws)
            assert initial["type"] == "CHARACTER_CREATION"
            assert initial["payload"]["scene_index"] == 0

            # ---- Scene 0 (the_roll): phase=continue → advances to the_arrangement.
            # the_arrangement is the scene that surfaces ``pool`` for player
            # assignment (the_roll itself is narration; the pool is rolled at
            # builder construction and rendered when arrangement scene activates).
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "continue"},
                    "player_id": "",
                }
            )
            msg = _recv(ws)
            assert msg["type"] == "CHARACTER_CREATION"
            assert msg["payload"]["scene_index"] == 1  # the_arrangement
            assert msg["payload"].get("pool") is not None
            assert len(msg["payload"]["pool"]) == 6
            pool = list(msg["payload"]["pool"])

            # ---- Scene 1 (the_arrangement): assign sorted-desc into stat order.
            # Highest into STR guarantees Fighter qualifies.
            stat_order = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
            sorted_pool = sorted(pool, reverse=True)
            for stat, value in zip(stat_order, sorted_pool, strict=True):
                ws.send_json(
                    {
                        "type": "CHARACTER_CREATION",
                        "payload": {"phase": "arrange_assign", "stat": stat, "value": value},
                        "player_id": "",
                    }
                )
                _recv(ws)  # arrangement-state echo
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "arrange_confirm"},
                    "player_id": "",
                }
            )
            msg = _recv(ws)
            assert msg["type"] == "CHARACTER_CREATION"
            assert msg["payload"]["scene_index"] == 2  # the_calling
            assert msg["payload"]["choices"]

            # ---- Scene 2 (the_calling): pick first qualifying class.
            ws.send_json(_chargen_payload(choice="1"))
            msg = _recv(ws)
            assert msg["payload"]["phase"] == "scene"
            assert msg["payload"]["scene_index"] == 3  # the_story

            # ---- Scene 3 (the_story): identity capture via story_confirm.
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {
                        "phase": "story_confirm",
                        "pronouns": "she/her",
                        "background": "Raised in the caverns.",
                        "description": "Tall, scarred, watchful.",
                    },
                    "player_id": "",
                }
            )
            msg = _recv(ws)
            assert msg["payload"]["phase"] == "scene"
            assert msg["payload"]["scene_index"] == 4  # the_kit

            # ---- Scene 4 (the_kit): display-only.
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "continue"},
                    "player_id": "",
                }
            )
            msg = _recv(ws)
            assert msg["payload"]["phase"] == "scene"
            assert msg["payload"]["scene_index"] == 5  # the_mouth

            # ---- Scene 5 (the_mouth): display-only, advances to Confirmation.
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "continue"},
                    "player_id": "",
                }
            )
            summary_msg = _recv(ws)
            assert summary_msg["type"] == "CHARACTER_CREATION"
            assert summary_msg["payload"]["phase"] == "confirmation"
            summary = summary_msg["payload"]["summary"]
            assert summary is not None
            # Lobby-name fallback because caverns has no name-entry scene.
            assert "Name: Rux" in summary
            # Pronouns from the_story.
            assert "Pronouns: she/her" in summary
            # Rolled stats present (arrangement materialized them).
            assert "Stats: STR " in summary
            # The chosen class from the_calling — one of the four BX classes.
            assert any(cls in summary for cls in ("Fighter", "Mage", "Cleric", "Thief"))

            # ---- Confirmation commit → complete ---------------------------
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "confirmation"},
                    "player_id": "",
                }
            )
            # Confirmation now emits a sequence of frames (CHARACTER_CREATION
            # complete, PARTY_STATUS, NARRATION × 2, NARRATION_END, ...). The
            # CHARACTER_CREATION{complete} frame is the first one. Drain
            # frames until we see it.
            complete = None
            for _ in range(12):
                next_msg = _recv(ws)
                if next_msg["type"] == "CHARACTER_CREATION":
                    complete = next_msg
                    break
            assert complete is not None
            assert complete["type"] == "CHARACTER_CREATION"
            assert complete["payload"]["phase"] == "complete"
            assert complete["payload"]["character"] is not None
            character = complete["payload"]["character"]
            # Character name is the lobby-provided name (caverns has no
            # name-entry scene; fallback chain is scene > lobby > "Player").
            assert character["core"]["name"] == "Rux"
            assert character["char_class"] in {"Fighter", "Mage", "Cleric", "Thief"}


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
            ws.send_json(_connect_payload(client, "elemental_harmony", "burning_peace"))
            connected = _recv(ws)
            assert connected["payload"]["event"] == "connected"
            assert connected["payload"]["has_character"] is False
            _recv(ws)  # initial chargen scene 0 kickoff

            # scene 0 has choices — send choice=1
            ws.send_json(_chargen_payload(choice="1"))
            msg = _recv(ws)
            assert msg["type"] == "CHARACTER_CREATION"
            # Either next scene or confirmation — both legitimate depending on
            # whether scene 0 has a hook_prompt (which would route to
            # AwaitingFollowup).
            assert msg["payload"]["phase"] in ("scene", "confirmation")

    def test_invalid_choice_returns_error_does_not_disconnect(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        client = _make_client(tmp_path)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_connect_payload(client, "elemental_harmony", "burning_peace"))
            _recv(ws)  # connected
            _recv(ws)  # initial chargen scene 0 kickoff

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
            ws.send_json(_connect_payload(client, "elemental_harmony", "burning_peace"))
            _recv(ws)  # connected
            _recv(ws)  # initial chargen scene 0 kickoff

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
            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "scene", "action": "back"},
                    "player_id": "",
                }
            )
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
            ws.send_json(_connect_payload(client, "caverns_and_claudes", "flickering_reach"))
            _recv(ws)
            _recv(ws)  # initial chargen scene 0 kickoff

            ws.send_json(
                {
                    "type": "CHARACTER_CREATION",
                    "payload": {"phase": "scene", "action": "bogus"},
                    "player_id": "",
                }
            )
            err = _recv(ws)
            assert err["type"] == "ERROR"
            assert "Unknown chargen action" in err["payload"]["message"]
