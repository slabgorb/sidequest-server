"""Tests for the_arrangement phase dispatch (Task 4.2).

End-to-end test of arrange_assign / arrange_clear / arrange_confirm /
arrange_reject phases through CharacterCreationHandler →
WebSocketSessionHandler → CharacterBuilder. Drives the dispatch handler
directly (no WebSocket layer) against the loaded caverns_and_claudes pack.
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


async def _walk_to_arrangement(handler: WebSocketSessionHandler) -> None:
    """Walk from the_roll (display-only) to the_arrangement."""
    sd = handler._session_data  # type: ignore[attr-defined]
    builder = sd.builder
    assert builder is not None
    while builder.current_scene().id != "the_arrangement":
        scene = builder.current_scene()
        if scene.choices:
            out = await _send(handler, CharacterCreationPayload(phase="scene", choice="1"))
        elif scene.allows_freeform:
            out = await _send(handler, CharacterCreationPayload(phase="scene", choice="x"))
        else:
            out = await _send(handler, CharacterCreationPayload(phase="continue"))
        assert not isinstance(out[0], ErrorMessage), getattr(out[0].payload, "message", out[0])


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# arrange_assign
# ---------------------------------------------------------------------------


class TestArrangeAssign:
    def test_assign_moves_value_into_slot_and_updates_payload(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            pool_before = list(builder.arrangement_pool())
            value = pool_before[0]
            out = await _send(
                handler,
                CharacterCreationPayload(phase="arrange_assign", stat="STR", value=value),
            )
            assert len(out) == 1
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage), msg
            payload = msg.payload
            assert payload.input_type == "stat_arrange"
            assert payload.assignment is not None
            assert payload.assignment["STR"] == value
            assert payload.pool is not None
            assert payload.pool.count(value) == pool_before.count(value) - 1
            # qualifying_classes and confirm_enabled must be present
            assert payload.qualifying_classes is not None
            assert payload.class_requirements is not None
            assert payload.confirm_enabled is False  # not all six filled yet

        run(body())

    def test_assign_missing_fields_returns_error(self, handler: WebSocketSessionHandler) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            out = await _send(handler, CharacterCreationPayload(phase="arrange_assign", stat="STR"))
            assert isinstance(out[0], ErrorMessage)

        run(body())

    def test_assign_value_not_in_pool_returns_error(self, handler: WebSocketSessionHandler) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            out = await _send(
                handler,
                CharacterCreationPayload(phase="arrange_assign", stat="STR", value=99),
            )
            assert isinstance(out[0], ErrorMessage)

        run(body())


# ---------------------------------------------------------------------------
# arrange_clear
# ---------------------------------------------------------------------------


class TestArrangeClear:
    def test_clear_returns_value_to_pool(self, handler: WebSocketSessionHandler) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            pool_before = list(builder.arrangement_pool())
            value = pool_before[0]
            await _send(
                handler,
                CharacterCreationPayload(phase="arrange_assign", stat="STR", value=value),
            )
            out = await _send(handler, CharacterCreationPayload(phase="arrange_clear", stat="STR"))
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage)
            assert msg.payload.assignment["STR"] is None
            assert sorted(msg.payload.pool) == sorted(pool_before)

        run(body())

    def test_clear_missing_stat_returns_error(self, handler: WebSocketSessionHandler) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            out = await _send(handler, CharacterCreationPayload(phase="arrange_clear"))
            assert isinstance(out[0], ErrorMessage)

        run(body())


# ---------------------------------------------------------------------------
# arrange_reject
# ---------------------------------------------------------------------------


class TestArrangeReject:
    def test_reject_clears_assignment_and_rerolls_pool(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            pool_before = list(builder.arrangement_pool())
            value = pool_before[0]
            # Fill one slot first
            await _send(
                handler,
                CharacterCreationPayload(phase="arrange_assign", stat="STR", value=value),
            )
            out = await _send(handler, CharacterCreationPayload(phase="arrange_reject"))
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage)
            # All slots empty after reject
            assert all(v is None for v in msg.payload.assignment.values())
            # Pool has 6 fresh values
            assert len(msg.payload.pool) == 6

        run(body())


# ---------------------------------------------------------------------------
# arrange_confirm
# ---------------------------------------------------------------------------


def _fill_all_six_qualifying(builder, send_fn, content):
    """Helper to fill all 6 slots with a qualifying arrangement."""
    pass


class TestArrangeConfirm:
    def test_confirm_with_complete_qualifying_arrangement_advances_to_calling(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            stats = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
            # Drive deterministic-ish: pick highest pool value for STR, then
            # next-highest for DEX, etc. (ensures any class with min ≤ that
            # value qualifies).
            pool = sorted(list(builder.arrangement_pool()), reverse=True)
            for stat, v in zip(stats, pool, strict=True):
                out = await _send(
                    handler,
                    CharacterCreationPayload(phase="arrange_assign", stat=stat, value=v),
                )
                assert not isinstance(out[0], ErrorMessage), out[0]
            # All six filled; now confirm.
            out = await _send(handler, CharacterCreationPayload(phase="arrange_confirm"))
            msg = out[0]
            assert isinstance(msg, CharacterCreationMessage), msg
            # Now on the_calling — choice scene.
            assert builder.current_scene().id == "the_calling"
            assert msg.payload.input_type == "choice"
            assert msg.payload.choices is not None
            assert len(msg.payload.choices) >= 1

        run(body())

    def test_confirm_with_unfilled_arrangement_returns_error(
        self, handler: WebSocketSessionHandler
    ) -> None:
        async def body() -> None:
            await _connect(handler)
            await _walk_to_arrangement(handler)
            sd = handler._session_data  # type: ignore[attr-defined]
            builder = sd.builder
            # Only fill 1 of 6.
            value = builder.arrangement_pool()[0]
            await _send(
                handler,
                CharacterCreationPayload(phase="arrange_assign", stat="STR", value=value),
            )
            out = await _send(handler, CharacterCreationPayload(phase="arrange_confirm"))
            assert isinstance(out[0], ErrorMessage)
            # Still on the_arrangement scene.
            assert builder.current_scene().id == "the_arrangement"

        run(body())
