"""Tests for the_story phase dispatch (Task 4.2).

End-to-end test of story_autogen / story_confirm phases through
CharacterCreationHandler → WebSocketSessionHandler → CharacterBuilder.
Drives the dispatch handler directly (no WebSocket layer) against the
loaded caverns_and_claudes pack.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from tests.server.conftest import (
    mock_claude_client_factory as _mock_claude_client_factory,
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


@pytest.fixture
def handler(tmp_path: Path) -> WebSocketSessionHandler:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("caverns_and_claudes content not found")
    return WebSocketSessionHandler(
        claude_client_factory=_mock_claude_client_factory(),
        genre_pack_search_paths=[CONTENT_ROOT],
        save_dir=tmp_path,
    )


async def _connect(handler: WebSocketSessionHandler) -> None:
    from tests.server.conftest import attach_default_room_context, seed_slug_for_test

    slug = seed_slug_for_test(
        handler._save_dir, genre="caverns_and_claudes", world="caverns_sunden"
    )
    attach_default_room_context(handler)
    payload = SessionEventPayload(
        event="connect",
        player_name="TestPlayer",
        game_slug=slug,
    )
    msg = SessionEventMessage(payload=payload, player_id="")
    out = await handler.handle_message(msg)
    assert isinstance(out[0], SessionEventMessage)


async def _send(
    handler: WebSocketSessionHandler,
    payload: CharacterCreationPayload,
    player_id: str = "test-pid",
) -> list:
    msg = CharacterCreationMessage(payload=payload, player_id=player_id)
    return await handler.handle_message(msg)


async def _walk_to_story(handler: WebSocketSessionHandler) -> None:
    """Walk from the_roll all the way to the_story.

    the_roll → continue → the_arrangement → assign all six → confirm →
    the_calling → choice 1 → the_story.
    """
    sd = handler._session_data  # type: ignore[attr-defined]
    builder = sd.builder
    assert builder is not None

    # the_roll → the_arrangement (display-only)
    while builder.current_scene().id != "the_arrangement":
        scene = builder.current_scene()
        if scene.choices:
            await _send(handler, CharacterCreationPayload(phase="scene", choice="1"))
        elif scene.allows_freeform:
            await _send(handler, CharacterCreationPayload(phase="scene", choice="x"))
        else:
            await _send(handler, CharacterCreationPayload(phase="continue"))

    # the_arrangement: fill highest → STR, etc, then confirm
    stats = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
    pool = sorted(list(builder.arrangement_pool()), reverse=True)
    for stat, v in zip(stats, pool, strict=True):
        await _send(
            handler,
            CharacterCreationPayload(phase="arrange_assign", stat=stat, value=v),
        )
    await _send(handler, CharacterCreationPayload(phase="arrange_confirm"))
    assert builder.current_scene().id == "the_calling"

    # the_calling: pick the first qualifying choice.
    await _send(handler, CharacterCreationPayload(phase="scene", choice="1"))
    assert builder.current_scene().id == "the_story"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Scene render — story_autogen does not advance scene
# ---------------------------------------------------------------------------


class TestStoryScenePayload:
    def test_initial_story_scene_has_story_input_type(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_story(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            msg = builder.to_scene_message("pid")
            assert msg.payload.input_type == "story"
            assert msg.payload.pronouns_options == ["she/her", "he/him", "they/them"]
            assert msg.payload.pronouns_allow_freeform is True
            assert msg.payload.background_optional is True
            assert msg.payload.description_optional is True
            assert msg.payload.autogen_available is True

        run(body())


# ---------------------------------------------------------------------------
# story_autogen
# ---------------------------------------------------------------------------


class TestStoryAutogen:
    def test_autogen_returns_payload_with_autogen_result_filled(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_story(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder

            out = await _send(handler, CharacterCreationPayload(phase="story_autogen", seed=42))
            assert len(out) == 1
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage), msg
            # Builder did NOT advance — still on the_story.
            assert builder.current_scene().id == "the_story"
            # autogen_result populated; the_story render still has story input_type.
            assert msg.payload.input_type == "story"
            assert msg.payload.autogen_result is not None
            assert "background" in msg.payload.autogen_result
            assert "description" in msg.payload.autogen_result

        run(body())


# ---------------------------------------------------------------------------
# story_confirm
# ---------------------------------------------------------------------------


class TestStoryConfirm:
    def test_confirm_with_pronouns_advances_to_kit(self, handler: WebSocketSessionHandler) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_story(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder

            out = await _send(
                handler,
                CharacterCreationPayload(
                    phase="story_confirm",
                    pronouns="they/them",
                    background="A wandering mendicant.",
                    description="Cloaked, scarred, soft-spoken.",
                ),
            )
            msg = out[0]
            assert not isinstance(msg, ErrorMessage), getattr(msg.payload, "message", msg)
            # the_story is required-pronouns; with they/them this should advance.
            assert builder.current_scene().id == "the_kit"

        run(body())

    def test_confirm_with_blank_pronouns_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_story(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder

            out = await _send(
                handler,
                CharacterCreationPayload(
                    phase="story_confirm",
                    pronouns="",
                    background="x",
                    description="y",
                ),
            )
            assert isinstance(out[0], ErrorMessage)
            # Still on the_story.
            assert builder.current_scene().id == "the_story"

        run(body())
