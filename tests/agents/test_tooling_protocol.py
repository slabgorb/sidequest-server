"""Shape tests for the ToolingLlmClient protocol + payload dataclasses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidequest.agents.tooling_protocol import (
    CacheableBlock,
    Message,
    ToolDefinition,
    ToolingLlmClient,
    ToolingResult,
    ToolResultBlock,
    ToolUseBlock,
)


def test_cacheable_block_is_frozen_dataclass() -> None:
    block = CacheableBlock(text="hi", cache=True)
    with pytest.raises(FrozenInstanceError):
        block.text = "bye"  # type: ignore[misc]


def test_cacheable_block_defaults_cache_false() -> None:
    block = CacheableBlock(text="hi")
    assert block.cache is False


def test_message_roles() -> None:
    user = Message(role="user", content="hello")
    assistant = Message(role="assistant", content="hi back")
    assert user.role == "user"
    assert assistant.role == "assistant"


def test_tool_definition_has_json_schema_field() -> None:
    td = ToolDefinition(
        name="roll_dice",
        description="Roll dice.",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    assert td.name == "roll_dice"
    assert td.input_schema["type"] == "object"


def test_tool_use_block_carries_id_and_args() -> None:
    b = ToolUseBlock(id="toolu_abc", name="roll_dice", arguments={"sides": 20})
    assert b.id == "toolu_abc"
    assert b.arguments["sides"] == 20


def test_tool_result_block_pairs_with_tool_use_id() -> None:
    r = ToolResultBlock(tool_use_id="toolu_abc", content="rolled 17", is_error=False)
    assert r.tool_use_id == "toolu_abc"
    assert r.is_error is False


def test_tooling_result_exposes_text_and_usage() -> None:
    res = ToolingResult(
        text="The dice show 17.",
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=20,
        cached_input_read_tokens=80,
        cached_input_write_tokens=0,
        model="claude-sonnet-4-6",
        tool_calls=[],
    )
    assert res.stop_reason == "end_turn"
    assert res.cached_input_read_tokens == 80


def test_tooling_llm_client_is_protocol() -> None:
    # Protocol is structural — it must accept a duck-typed conformer.
    assert ToolingLlmClient is not None
    # No instantiation; protocols are not constructible.
