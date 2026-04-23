"""Shared pytest fixtures for sidequest-server server-layer tests.

Centralizes the Claude-client mock used by every dispatch test. Before
Story 2.3 Slice H the mock could be a bare ``AsyncMock()`` because
``_chargen_confirmation`` never invoked the orchestrator — the
narration path only fired on PLAYER_ACTION. Slice H routes an opening
turn through the orchestrator at confirmation, so every chargen test
now goes through the narrator pipeline and the mock has to return a
real :class:`ClaudeResponse` with non-empty text + session id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse


def canned_claude_response(
    *,
    text: str | None = None,
    session_id: str = "test-session",
) -> ClaudeResponse:
    """Build a minimally-valid :class:`ClaudeResponse` for narration tests.

    The orchestrator's ``game_patch`` extraction regex runs on
    ``text``; an empty / missing fence block is fine — extraction
    falls back to ``{}`` and the narration pipeline completes. Tests
    that care about state deltas override ``text`` to include a
    ```game_patch``` fence.
    """
    return ClaudeResponse(
        text=text
        or (
            "The world takes shape around you. Light filters through the "
            "morning haze and the day begins.\n\n"
            "```game_patch\n{}\n```"
        ),
        session_id=session_id,
        input_tokens=100,
        output_tokens=60,
    )


def make_mock_claude_client(
    *,
    text: str | None = None,
    session_id: str = "test-session",
) -> MagicMock:
    """Return a Claude client mock with ``send_with_session`` wired to
    yield a canned :class:`ClaudeResponse`.

    Tests that want to inspect the prompt sent to Claude can access
    ``mock.send_with_session`` (an :class:`AsyncMock`) and its
    ``call_args`` after invocation.
    """
    mock = MagicMock()
    mock.send_with_session = AsyncMock(
        return_value=canned_claude_response(text=text, session_id=session_id)
    )
    return mock


def mock_claude_client_factory(
    *,
    text: str | None = None,
    session_id: str = "test-session",
):
    """Factory suitable for ``WebSocketSessionHandler(claude_client_factory=...)``."""
    client = make_mock_claude_client(text=text, session_id=session_id)
    return lambda: client


@pytest.fixture
def session_handler_factory(tmp_path):
    """Return a factory callable ``(genre: str) -> (sd, handler)``.

    Builds a minimal ``_SessionData`` + ``WebSocketSessionHandler`` suitable
    for unit-testing ``_execute_narration_turn`` without a real WebSocket or
    LLM call. The test is responsible for overriding
    ``sd.orchestrator.run_narration_turn`` with an ``AsyncMock``.

    Task 11 (story 3.4): used by test_confrontation_dispatch_wiring.py.
    Task 16 (story 3.4): snapshot now includes a Character named "Rux" so
    XP-award tests can inspect ``sd.snapshot.characters[0].core.xp``.
    """
    from pathlib import Path

    from sidequest.agents.orchestrator import Orchestrator
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory
    from sidequest.game.persistence import SqliteStore
    from sidequest.game.session import GameSnapshot
    from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS, GenreLoader
    from sidequest.server.session_handler import (
        WebSocketSessionHandler,
        _SessionData,
    )

    def _make(genre: str):
        pack = GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load(genre)
        snap = GameSnapshot(genre_slug=genre)
        core = CreatureCore(
            name="Rux",
            description="A stoic fighter",
            personality="stoic",
            inventory=Inventory(),
        )
        char = Character(
            core=core,
            char_class="Fighter",
            race="Human",
            backstory="A wandering fighter",
        )
        snap.characters.append(char)
        store = SqliteStore.open_in_memory()
        orch = MagicMock(spec=Orchestrator)
        sd = _SessionData(
            genre_slug=genre,
            world_slug="",
            player_name="Rux",
            player_id="player-1",
            snapshot=snap,
            store=store,
            genre_pack=pack,
            orchestrator=orch,
        )
        handler = WebSocketSessionHandler(save_dir=tmp_path)
        handler._session_data = sd
        return sd, handler

    return _make
