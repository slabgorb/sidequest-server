"""PerceptionFilter — Phase B primitive.

The narrator's tool path runs every tool result through a PerceptionFilter
before handing it back to the model. The Noop default passes everything
through unchanged; Phase C wires per-tool filter rules (see spec §Perception
filtering at the tool layer for the per-tool rule table).

This is the narrator-path successor to ADR-028's *envisioned* post-pass
LLM rewriter — which never actually got built on the narrator side.
``sidequest/agents/perception_rewriter.py`` is a separate, deterministic
span-strip pass that runs in the MP fan-out emitter
(``sidequest/server/emitters.py``) for status-effect-based fidelity
override (blinded/deafened/invisible) on broadcast messages. That module
is independent of the narrator path and survives Phase D — see ADR-104
for the doctrine split.

Write tools' results are intentionally not redacted: mutation status must
be objectively reported. The filter inspects category to decide whether
redaction is in scope.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sidequest.agents.tool_registry import ToolCategory, ToolResult


@runtime_checkable
class PerceptionFilter(Protocol):
    def filter_result(
        self,
        *,
        tool_name: str,
        category: ToolCategory,
        result: ToolResult,
        perspective_pc: str | None,
    ) -> ToolResult: ...


class NoopPerceptionFilter:
    """Default — pass through. Use in tests + Phase B integration."""

    def filter_result(
        self,
        *,
        tool_name: str,
        category: ToolCategory,
        result: ToolResult,
        perspective_pc: str | None,
    ) -> ToolResult:
        return result
