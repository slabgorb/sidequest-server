"""ADR-098: MP merged turns run stateless, same path as single-PC turns."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_mp_merged_turn_is_stateless(simple_turn_context):
    """A merged turn renders multi-PC declarations into user_message; no session reads/writes."""
    ctx = replace(
        simple_turn_context,
        merged_player_actions=[
            ("Laverne", "I look at Shirley."),
            ("Shirley", "I look back."),
            ("Lenny", "I check the door."),
        ],
    )

    client = AsyncMock()
    captured: dict[str, str] = {}

    async def capture(system_prompt: str, user_message: str, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_message
        assert "session_id" not in kwargs
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client.send_stateless = AsyncMock(side_effect=capture)
    client.send_with_session = AsyncMock(side_effect=AssertionError("must not be called"))

    orch = Orchestrator(client=client)
    result = await orch._run_narration_turn_synchronous("(ignored in MP path)", ctx)

    assert "Laverne" in captured["user"]
    assert "Shirley" in captured["user"]
    assert "Lenny" in captured["user"]
    assert result.narration  # non-empty
