"""ADR-098 central wiring test: prompt size does not grow with turn count."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_prompt_size_bounded_over_30_turns(simple_turn_context):
    """Run 30 simulated turns; assert prompt size does not grow monotonically.

    Acceptance:
      - No strict monotonic increase: there exists at least one N where
        turn[N+1] size <= turn[N] size.
      - max(sizes) / min(sizes) <= 1.5: the largest turn is no more
        than 50% larger than the smallest.
    """
    sizes: list[int] = []

    async def capture(system_prompt: str, user_message: str, **kwargs):
        sizes.append(len(system_prompt) + len(user_message))
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client = AsyncMock()
    client.send_stateless = AsyncMock(side_effect=capture)

    orch = Orchestrator(client=client)
    for turn_n in range(30):
        ctx = replace(simple_turn_context, turn_number=turn_n)
        await orch._run_narration_turn_synchronous(f"turn {turn_n} action", ctx)

    assert len(sizes) == 30

    # Condition 1: not strictly monotonically increasing.
    strictly_growing = all(sizes[i + 1] > sizes[i] for i in range(len(sizes) - 1))
    assert not strictly_growing, (
        f"Prompt size grew on every single turn — strict monotonic growth: {sizes}"
    )

    # Condition 2: bounded range.
    ratio = max(sizes) / min(sizes)
    assert ratio <= 1.5, (
        f"Prompt size range too wide: max={max(sizes)} min={min(sizes)} ratio={ratio:.2f}"
    )
