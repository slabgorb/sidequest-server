"""Tests that the narrator path makes stateless outbound calls (ADR-098)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator


def _fake_response(text: str = '{"narration": "ok"}') -> ClaudeResponse:
    return ClaudeResponse(text=text, session_id=None)


def test_process_action_calls_send_stateless_never_send_with_session(simple_turn_context):
    """Narrator path must never invoke send_with_session — only send_stateless."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(return_value=_fake_response())
    client.send_with_session = AsyncMock(
        side_effect=AssertionError(
            "send_with_session must not be called from narrator path post-ADR-098"
        )
    )

    orch = Orchestrator(client=client)
    asyncio.run(orch._run_narration_turn_synchronous("look around", simple_turn_context))

    assert client.send_stateless.call_count == 1
    client.send_with_session.assert_not_called()


def test_send_stateless_call_carries_system_and_user(simple_turn_context):
    """system_prompt is the stable scaffold; user_message contains the player action."""
    client = AsyncMock()
    client.send_stateless = AsyncMock(return_value=_fake_response())

    orch = Orchestrator(client=client)
    asyncio.run(orch._run_narration_turn_synchronous("look around", simple_turn_context))

    call = client.send_stateless.await_args
    assert "system_prompt" in call.kwargs
    assert "user_message" in call.kwargs
    assert "session_id" not in call.kwargs
    # The player's action appears in user_message (via the player_action section).
    assert "look around" in call.kwargs["user_message"]
    # The stable scaffold lives in system_prompt — narrator identity is one
    # of the load-bearing pinned-stable sections.
    assert call.kwargs["system_prompt"] != ""
