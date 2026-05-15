"""Tool registry — Phase B foundation.

@tool decorator + ToolContext + ToolResult + Registry + dispatch.
Phase C populates the v1 catalog by importing each adapter from
sidequest.agents.tools.<name>.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from sidequest.agents.perception_filter import (  # type: ignore[import-not-found]
        PerceptionFilter,
    )


class ToolCategory(StrEnum):
    READ = "read"
    WRITE = "write"
    GENERATE = "generate"


class ToolResultStatus(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR_RECOVERABLE = "error_recoverable"
    ERROR_FATAL = "error_fatal"


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ToolResultStatus
    payload: Any | None = None
    message: str | None = None

    @classmethod
    def ok(cls, payload: Any) -> ToolResult:
        return cls(status=ToolResultStatus.OK, payload=payload)

    @classmethod
    def not_found(cls, message: str) -> ToolResult:
        return cls(status=ToolResultStatus.NOT_FOUND, message=message)

    @classmethod
    def error(cls, message: str, *, recoverable: bool = True) -> ToolResult:
        status = (
            ToolResultStatus.ERROR_RECOVERABLE
            if recoverable
            else ToolResultStatus.ERROR_FATAL
        )
        return cls(status=status, message=message)

    def to_anthropic_payload(self) -> tuple[str, bool]:
        """Render as (content_str, is_error) for the SDK tool_result message.

        Non-JSON-serializable payload values are coerced to str via json.dumps(default=str).
        """
        if self.status is ToolResultStatus.OK:
            return (json.dumps(self.payload, default=str), False)
        if self.status is ToolResultStatus.NOT_FOUND:
            return (f"NOT_FOUND: {self.message}", False)
        # error_recoverable / error_fatal
        return (f"ERROR: {self.message}", True)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Runtime state injected into each tool handler.

    The model never sees ToolContext — it is the server-side companion to
    the JSON-Schema-validated args the model does see.
    """

    world_id: str
    session_id: str
    perspective_pc: str | None
    turn_number: int
    store: Any  # SqliteStore — kept Any to avoid Phase B coupling
    otel_span: Span
    perception_filter: PerceptionFilter
