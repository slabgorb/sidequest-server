"""Phase B wiring test — registry + SDK client + dispatch round-trip."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from sidequest.agents.anthropic_sdk_client import AnthropicSdkClient
from sidequest.agents.perception_filter import NoopPerceptionFilter
from sidequest.agents.tool_registry import (
    Registry,
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)
from sidequest.agents.tooling_protocol import (
    CacheableBlock,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _Text:
    type: str
    text: str


@dataclass
class _ToolUse:
    type: str
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class _Resp:
    content: list[Any]
    stop_reason: str
    usage: _Usage
    model: str


class _Msgs:
    def __init__(self, responses: list[_Resp]) -> None:
        self._r = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return self._r.pop(0)


class _Sdk:
    def __init__(self, responses: list[_Resp]) -> None:
        self.messages = _Msgs(responses)


class _DiceArgs(BaseModel):
    sides: int = Field(..., gt=0)


async def test_registry_round_trip_via_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg = Registry()

    @tool(
        name="roll_dice",
        description="Roll dice.",
        category=ToolCategory.GENERATE,
        registry=reg,
    )
    async def roll(args: _DiceArgs, ctx: ToolContext) -> ToolResult:
        return ToolResult.ok({"value": args.sides})

    sdk = _Sdk(
        responses=[
            _Resp(
                content=[
                    _ToolUse(
                        type="tool_use",
                        id="t1",
                        name="roll_dice",
                        input={"sides": 20},
                    )
                ],
                stop_reason="tool_use",
                usage=_Usage(input_tokens=200, output_tokens=15),
                model="claude-sonnet-4-6",
            ),
            _Resp(
                content=[_Text(type="text", text="A natural 20.")],
                stop_reason="end_turn",
                usage=_Usage(input_tokens=220, output_tokens=10),
                model="claude-sonnet-4-6",
            ),
        ]
    )
    client = AnthropicSdkClient(sdk=sdk)
    ctx = ToolContext(
        world_id="w",
        session_id="s",
        perspective_pc="alex",
        turn_number=1,
        store=MagicMock(),
        otel_span=MagicMock(),
        perception_filter=NoopPerceptionFilter(),
    )

    async def dispatch(block: ToolUseBlock) -> ToolResultBlock:
        return await reg.dispatch(block, ctx)

    result = await client.complete_with_tools(
        system_blocks=[CacheableBlock(text="rules", cache=True)],
        messages=[Message(role="user", content="roll d20")],
        tools=reg.tool_definitions(),
        tool_dispatch=dispatch,
        model="claude-sonnet-4-6",
    )
    assert result.text == "A natural 20."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "roll_dice"
