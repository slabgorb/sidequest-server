"""Unit tests for strip_combat_brackets helper and wiring.

Port of sidequest-api behavior for Story 3.4 Task 12: strip [combat]
markers from aside prose before narrator dispatch.
"""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from sidequest.agents.orchestrator import NarrationTurnResult
from sidequest.protocol.messages import (
    PlayerActionMessage,
    PlayerActionPayload,
)
from sidequest.server.dispatch.combat_brackets import strip_combat_brackets
from sidequest.server.session_handler import _State
from sidequest.protocol.types import NonBlankString


def test_strip_removes_leading_combat_bracket() -> None:
    assert strip_combat_brackets("[combat] I swing my sword") == "I swing my sword"


def test_strip_removes_embedded_combat_bracket() -> None:
    # Rust behaviour: any [combat] tag is scrubbed; surrounding whitespace preserved.
    assert strip_combat_brackets("foo [combat] bar") == "foo  bar"


def test_strip_preserves_non_combat_brackets() -> None:
    assert strip_combat_brackets("[chase] run!") == "[chase] run!"


def test_strip_is_case_insensitive_on_tag() -> None:
    assert strip_combat_brackets("[COMBAT] attack") == "attack"
    assert strip_combat_brackets("[Combat] attack") == "attack"


def test_strip_empty_returns_empty() -> None:
    assert strip_combat_brackets("") == ""


# ---------------------------------------------------------------------------
# Wiring tests — PLAYER_ACTION path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aside_player_action_strips_brackets_before_narrator(
    session_handler_factory,
):
    """PLAYER_ACTION with aside=True routes through strip_combat_brackets."""
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="ok"),
    )
    # Force handler into Playing state so _handle_player_action runs the
    # narration path rather than rejecting.
    handler._state = _State.Playing
    msg = PlayerActionMessage(
        payload=PlayerActionPayload(
            action=NonBlankString.model_validate("[combat] I whisper to James"),
            aside=True,
        ),
        player_id="player-1",
    )
    await handler._handle_player_action(msg)

    seen_action = sd.orchestrator.run_narration_turn.call_args[0][0]
    assert "[combat]" not in seen_action
    assert "whisper to James" in seen_action


@pytest.mark.asyncio
async def test_non_aside_player_action_does_not_strip(
    session_handler_factory,
):
    """PLAYER_ACTION with aside=False leaves [combat] markers intact."""
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=NarrationTurnResult(narration="ok"),
    )
    handler._state = _State.Playing
    msg = PlayerActionMessage(
        payload=PlayerActionPayload(
            action=NonBlankString.model_validate("[combat] I whisper to James"),
            aside=False,
        ),
        player_id="player-1",
    )
    await handler._handle_player_action(msg)
    seen_action = sd.orchestrator.run_narration_turn.call_args[0][0]
    # Non-aside: bracket must be preserved — it's the player's literal prose.
    assert "[combat]" in seen_action
