"""Tests for WebSocketSessionHandler._handle_character_creation — Slice D (Story 2.2).

Drives the chargen dispatch handler directly (no WebSocket layer) against a
real loaded genre pack. Covers:
- Builder initialization at connect time (Creating state)
- Action routing: back / edit / unknown
- phase=scene: numeric choice, case-insensitive label match, freeform input,
  transition to next scene or confirmation summary
- phase=continue: apply_auto_advance, transition
- phase=confirmation: builder.build, character appended to snapshot, complete
  message wire shape
- Structured error responses on every failure path (never exceptions through
  the WebSocket contract)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler


CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_claude_client_factory():
    mock = MagicMock()
    mock.send_with_session = AsyncMock()
    return lambda: mock


@pytest.fixture
def handler(tmp_path: Path) -> WebSocketSessionHandler:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")
    return WebSocketSessionHandler(
        claude_client_factory=_mock_claude_client_factory(),
        genre_pack_search_paths=[CONTENT_ROOT],
        save_dir=tmp_path,
    )


async def _connect(
    handler: WebSocketSessionHandler,
    *,
    genre: str = "caverns_and_claudes",
    world: str = "flickering_reach",
    player_name: str = "TestPlayer",
) -> SessionEventMessage:
    payload = SessionEventPayload(
        event="connect",
        player_name=player_name,
        genre=genre,
        world=world,
    )
    msg = SessionEventMessage(payload=payload, player_id="")
    out = await handler.handle_message(msg)
    assert len(out) == 1
    connected = out[0]
    assert isinstance(connected, SessionEventMessage)
    assert connected.payload.event == "connected"
    return connected


async def _send_chargen(
    handler: WebSocketSessionHandler,
    payload: CharacterCreationPayload,
    player_id: str = "test-pid",
) -> list:
    msg = CharacterCreationMessage(payload=payload, player_id=player_id)
    return await handler.handle_message(msg)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Connect initializes the builder
# ---------------------------------------------------------------------------


class TestConnectInitBuilder:
    def test_connect_to_caverns_creates_builder(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            connected = await _connect(handler)
            assert connected.payload.has_character is False
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None
            assert sd.builder is not None
            assert sd.builder.total_scenes() > 0

        run(body())

    def test_connect_without_chargen_leaves_builder_none(
        self, tmp_path: Path
    ) -> None:
        # A pack with no char_creation scenes shouldn't construct a builder.
        # We simulate by pointing at the real path but the pack will have
        # scenes — so we stub via a handler that overrides the genre loader.
        # Instead: assert the is-None path by constructing a handler with a
        # manipulated genre pack via direct _SessionData injection, which is
        # covered by the existing websocket mock tests. This test therefore
        # checks the positive case and delegates the null case to the
        # existing fixture patterns.
        pytest.skip(
            "covered by tests/server/test_websocket.py — mock pack with "
            "char_creation=[] already asserts the None-builder path"
        )


# ---------------------------------------------------------------------------
# Phase dispatch — scene
# ---------------------------------------------------------------------------


class TestPhaseScene:
    def test_numeric_choice_advances_scene(self, tmp_path: Path) -> None:
        # Scene 0 must have choices for this path — caverns scene 0 is
        # display-only (auto_advance), so elemental_harmony is the right fixture.
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        handler = WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

        async def body() -> None:
            await _connect(handler, genre="elemental_harmony", world="burning_peace")
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None and sd.builder is not None
            if not sd.builder.current_scene().choices:
                pytest.skip("elemental_harmony scene 0 has no choices")
            out = await _send_chargen(
                handler,
                CharacterCreationPayload(phase="scene", choice="1"),
            )
            assert len(out) == 1
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage)
            assert msg.payload.phase in ("scene", "confirmation")

        run(body())

    def test_invalid_numeric_choice_returns_error(self, tmp_path: Path) -> None:
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        handler = WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

        async def body() -> None:
            await _connect(handler, genre="elemental_harmony", world="burning_peace")
            out = await _send_chargen(
                handler,
                CharacterCreationPayload(phase="scene", choice="999"),
            )
            assert len(out) == 1
            assert isinstance(out[0], ErrorMessage)
            assert "Invalid choice" in str(out[0].payload.message) or "invalid" in str(out[0].payload.message).lower()

        run(body())

    def test_missing_choice_defaults_to_first(self, tmp_path: Path) -> None:
        # Rust default: `payload.choice.as_deref().unwrap_or("1")`.
        if not (CONTENT_ROOT / "elemental_harmony").is_dir():
            pytest.skip("elemental_harmony content not found")
        handler = WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

        async def body() -> None:
            await _connect(handler, genre="elemental_harmony", world="burning_peace")
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="scene")
            )
            assert len(out) == 1
            assert not isinstance(out[0], ErrorMessage)

        run(body())

    def test_label_match_case_insensitive(self, tmp_path: Path) -> None:
        # Use elemental_harmony or mutant_wasteland — a pack with a choice-based scene 0.
        noir = CONTENT_ROOT / "elemental_harmony"
        if not noir.is_dir():
            pytest.skip("elemental_harmony content not found")

        handler = WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

        async def body() -> None:
            await _connect(handler, genre="elemental_harmony", world="burning_peace")
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None and sd.builder is not None
            if not sd.builder.current_scene().choices:
                pytest.skip("elemental_harmony scene 0 has no choices")
            label = sd.builder.current_scene().choices[0].label
            # Submit the label in lowercase — match must be case-insensitive.
            out = await _send_chargen(
                handler,
                CharacterCreationPayload(phase="scene", choice=label.lower()),
            )
            assert not isinstance(out[0], ErrorMessage)

        run(body())


# ---------------------------------------------------------------------------
# Phase dispatch — continue
# ---------------------------------------------------------------------------


class TestPhaseContinue:
    def test_continue_advances_display_only_scene(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            # caverns scene 0 (the_roll) is auto-advance / display-only — the
            # expected UI flow sends phase=continue.
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="continue")
            )
            assert len(out) == 1
            assert not isinstance(out[0], ErrorMessage)

        run(body())


# ---------------------------------------------------------------------------
# Phase dispatch — confirmation (commit)
# ---------------------------------------------------------------------------


class TestPhaseConfirmation:
    def test_confirmation_builds_character_and_emits_complete(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            assert builder is not None

            # Walk caverns to Confirmation: 4 scenes, all auto_advance/choice.
            while not builder.is_confirmation():
                if builder.is_in_progress():
                    scene = builder.current_scene()
                    if scene.choices:
                        out = await _send_chargen(
                            handler,
                            CharacterCreationPayload(phase="scene", choice="1"),
                        )
                    else:
                        # Display-only or freeform scene — continue or name entry.
                        if scene.allows_freeform:
                            out = await _send_chargen(
                                handler,
                                CharacterCreationPayload(
                                    phase="scene", choice="Rux"
                                ),
                            )
                        else:
                            out = await _send_chargen(
                                handler,
                                CharacterCreationPayload(phase="continue"),
                            )
                    assert not isinstance(out[0], ErrorMessage), (
                        f"unexpected error at scene {builder.current_scene_index()}: "
                        f"{getattr(out[0].payload, 'message', out[0])}"
                    )
                else:
                    pytest.fail(f"unexpected phase: {builder._phase!r}")

            # Now commit.
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="confirmation")
            )
            assert len(out) == 1
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage)
            assert msg.payload.phase == "complete"
            assert msg.payload.character is not None

            # Character landed on snapshot; builder is consumed.
            assert len(sd.snapshot.characters) == 1
            assert sd.builder is None

        run(body())


# ---------------------------------------------------------------------------
# Navigation actions — back / edit / unknown
# ---------------------------------------------------------------------------


class TestActions:
    def test_back_from_first_scene_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="scene", action="back")
            )
            assert isinstance(out[0], ErrorMessage)
            assert "Cannot go back" in str(out[0].payload.message)

        run(body())

    def test_edit_without_target_step_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="scene", action="edit")
            )
            assert isinstance(out[0], ErrorMessage)
            assert "target_step" in str(out[0].payload.message)

        run(body())

    def test_edit_out_of_range_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _send_chargen(
                handler,
                CharacterCreationPayload(
                    phase="scene", action="edit", target_step=999
                ),
            )
            assert isinstance(out[0], ErrorMessage)

        run(body())

    def test_unknown_action_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _send_chargen(
                handler,
                CharacterCreationPayload(phase="scene", action="bogus"),
            )
            assert isinstance(out[0], ErrorMessage)
            assert "Unknown chargen action" in str(out[0].payload.message)

        run(body())

    def test_back_after_advance_reverts_to_previous_scene(
        self, tmp_path: Path
    ) -> None:
        noir = CONTENT_ROOT / "elemental_harmony"
        if not noir.is_dir():
            pytest.skip("elemental_harmony content not found")

        handler = WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=tmp_path,
        )

        async def body() -> None:
            await _connect(handler, genre="elemental_harmony", world="burning_peace")
            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd is not None and sd.builder is not None
            if not sd.builder.current_scene().choices:
                pytest.skip("elemental_harmony scene 0 has no choices")
            before_idx = sd.builder.current_scene_index()
            await _send_chargen(
                handler, CharacterCreationPayload(phase="scene", choice="1")
            )
            # Might have transitioned to AwaitingFollowup or advanced scene;
            # either way, scene-walking should be able to go_back.
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="scene", action="back")
            )
            assert not isinstance(out[0], ErrorMessage)
            # We're back on a scene message.
            assert isinstance(out[0], CharacterCreationMessage)
            assert out[0].payload.phase == "scene"

        run(body())


# ---------------------------------------------------------------------------
# State-machine guards
# ---------------------------------------------------------------------------


class TestStateGuards:
    def test_chargen_before_connect_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="scene", choice="1")
            )
            assert isinstance(out[0], ErrorMessage)
            assert "AwaitingConnect" in str(out[0].payload.message)

        run(body())

    def test_unknown_phase_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            out = await _send_chargen(
                handler, CharacterCreationPayload(phase="mystery")
            )
            assert isinstance(out[0], ErrorMessage)
            assert "Unknown chargen phase" in str(out[0].payload.message)

        run(body())
