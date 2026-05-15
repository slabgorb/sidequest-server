"""AnthropicSdkClient — Phase A foundation.

Construction, auth, cache-TTL config, and the ToolingLlmClient seam.
complete_with_tools lands in the next task (Task 9).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Literal

from sidequest.agents.claude_client import LlmClientError
from sidequest.agents.tooling_protocol import (
    CacheableBlock,
    Message,
    ToolDefinition,
    ToolingResult,
    ToolResultBlock,
    ToolUseBlock,
)


class AnthropicSdkClientError(LlmClientError):
    """Base error from AnthropicSdkClient."""


class AnthropicSdkConfigError(AnthropicSdkClientError):
    """Construction-time configuration problem (missing key, bad TTL)."""


CacheTtl = Literal["5m", "1h"]
_VALID_TTLS: frozenset[str] = frozenset({"5m", "1h"})


class AnthropicSdkClient:
    """Anthropic SDK client implementing ToolingLlmClient.

    Construction is loud: missing ANTHROPIC_API_KEY raises on any path that
    didn't inject `sdk=` directly. Tests inject; production reads env.
    """

    def __init__(
        self,
        *,
        sdk: Any | None = None,
        cache_ttl: CacheTtl | None = None,
    ) -> None:
        self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        if sdk is None and not self._api_key:
            raise AnthropicSdkConfigError(
                "ANTHROPIC_API_KEY not set — required to construct "
                "AnthropicSdkClient without an explicit sdk= injection."
            )

        resolved_ttl: str
        if cache_ttl is not None:
            resolved_ttl = cache_ttl
        else:
            resolved_ttl = os.environ.get("SIDEQUEST_ANTHROPIC_CACHE_TTL", "5m")
        if resolved_ttl not in _VALID_TTLS:
            raise AnthropicSdkConfigError(
                f"SIDEQUEST_ANTHROPIC_CACHE_TTL={resolved_ttl!r} invalid; "
                f"must be one of {sorted(_VALID_TTLS)}"
            )
        self.cache_ttl: CacheTtl = resolved_ttl  # type: ignore[assignment]

        if sdk is None:
            from anthropic import AsyncAnthropic  # local import: avoid module load on test paths

            sdk = AsyncAnthropic(api_key=self._api_key)
        self._sdk = sdk

    @property
    def api_key_present(self) -> bool:
        return bool(self._api_key)

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
        raise NotImplementedError(
            "complete_with_tools lands in Task 9. Phase A Task 8 only "
            "covers construction + protocol seam."
        )
