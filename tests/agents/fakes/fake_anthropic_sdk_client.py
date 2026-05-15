"""FakeAnthropicSdkClient — scripted-response test double.

All non-API tests use this fake. Each test constructs the response sequence
the test scenario requires; the fake never reaches the network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sidequest.agents.tooling_protocol import (
    CacheableBlock,
    Message,
    ToolDefinition,
    ToolingResult,
    ToolResultBlock,
    ToolUseBlock,
)


class ScriptExhausted(RuntimeError):
    """The test asked the fake for more responses than were scripted."""


@dataclass(frozen=True, slots=True)
class ScriptedResponse:
    """One step of a multi-step model interaction."""

    text: str
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use", "error"]
    input_tokens: int
    output_tokens: int
    cached_input_read_tokens: int
    cached_input_write_tokens: int
    model: str
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    stream_deltas: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """A captured call into the fake, for assertion in tests."""

    model: str
    system_blocks: list[CacheableBlock]
    messages: list[Message]
    tools: list[ToolDefinition]


class FakeAnthropicSdkClient:
    """Implements ToolingLlmClient with scripted responses."""

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self._responses = list(responses)
        self._cursor = 0
        self.recorded_requests: list[RecordedRequest] = []

    async def complete_with_tools(
        self,
        system_blocks: list[CacheableBlock],
        messages: list[Message],
        tools: list[ToolDefinition],
        tool_dispatch: Callable[[ToolUseBlock], ToolResultBlock] | None = None,
        *,
        model: str,
        max_iterations: int = 8,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ToolingResult:
        all_tool_calls: list[ToolUseBlock] = []
        iterations = 0
        current_messages = list(messages)
        while True:
            iterations += 1
            if iterations > max_iterations:
                raise RuntimeError(
                    f"FakeAnthropicSdkClient exceeded max_iterations={max_iterations}"
                )
            if self._cursor >= len(self._responses):
                raise ScriptExhausted(
                    f"Fake ran out of scripted responses at iteration {iterations}"
                )
            response = self._responses[self._cursor]
            self._cursor += 1
            self.recorded_requests.append(
                RecordedRequest(
                    model=model,
                    system_blocks=list(system_blocks),
                    messages=list(current_messages),
                    tools=list(tools),
                )
            )
            if on_text_delta is not None:
                for chunk in response.stream_deltas:
                    on_text_delta(chunk)

            if response.stop_reason != "tool_use":
                return ToolingResult(
                    text=response.text,
                    stop_reason=response.stop_reason,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_read_tokens=response.cached_input_read_tokens,
                    cached_input_write_tokens=response.cached_input_write_tokens,
                    model=response.model,
                    tool_calls=all_tool_calls,
                )

            if tool_dispatch is None:
                raise RuntimeError(
                    "Scripted tool_use response requires tool_dispatch callback"
                )
            results: list[ToolResultBlock] = []
            for tu in response.tool_uses:
                all_tool_calls.append(tu)
                results.append(tool_dispatch(tu))
            current_messages = current_messages + [
                Message(
                    role="assistant",
                    content="[tool_use placeholder]",
                ),
                Message(
                    role="user",
                    content="\n".join(
                        f"[tool_result {r.tool_use_id}: {r.content}]" for r in results
                    ),
                ),
            ]
