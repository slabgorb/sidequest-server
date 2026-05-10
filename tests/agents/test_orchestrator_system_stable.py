"""ADR-098: system_prompt is byte-identical across turns of one game."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_system_prompt_identical_across_10_turns(simple_turn_context):
    captured_system: list[str] = []

    async def capture(system_prompt: str, user_message: str, **kwargs):
        captured_system.append(system_prompt)
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client = AsyncMock()
    client.send_stateless = AsyncMock(side_effect=capture)

    orch = Orchestrator(client=client)
    for turn_n in range(10):
        ctx = replace(simple_turn_context, turn_number=turn_n)
        await orch._run_narration_turn_synchronous(f"turn {turn_n} action", ctx)

    distinct = set(captured_system)
    assert len(distinct) == 1, (
        f"system_prompt should be byte-identical across all 10 turns; "
        f"got {len(distinct)} distinct values"
    )
