"""sidequest.agents — Claude CLI agent foundation.

Port of sidequest-agents crate (Phase 1 subset).
ADR-082: Python server narration vertical slice.

Phase 1 exports:
- Agent (Protocol), AgentResponse, BaseAgent, make_agent
- ClaudeClient, LlmClient, ClaudeClientBuilder, ClaudeResponse
- ClaudeClientError, TimeoutError, SubprocessFailed, EmptyResponse
- PromptComposer, PromptRegistry, PromptSection
- AttentionZone, SectionCategory, RuleTier
- SoulData, SoulPrinciple, parse_soul_md
- NarratorAgent (Phase 1 — story 41-5)
- Orchestrator, TurnContext, NarrationTurnResult, run_narration_turn (Phase 1 — story 41-5)
- ActionRewrite, BeatSelection, VisualScene, NpcMention (Phase 1 — story 41-5)
"""

from __future__ import annotations

from sidequest.agents.agent import Agent, AgentResponse, BaseAgent, make_agent
from sidequest.agents.claude_client import (
    ClaudeClient,
    ClaudeClientBuilder,
    ClaudeClientError,
    ClaudeResponse,
    EmptyResponse,
    LlmCapabilities,
    LlmClient,
    LlmClientError,
    SubprocessFailed,
)
from sidequest.agents.claude_client import TimeoutError as ClaudeTimeoutError
from sidequest.agents.llm_factory import UnknownBackend, build_llm_client
from sidequest.agents.local_dm import LocalDM
from sidequest.agents.narrator import NarratorAgent, narrator_output_format_text
from sidequest.agents.ollama_client import OllamaClient, OllamaClientError
from sidequest.agents.orchestrator import (
    ActionRewrite,
    BeatSelection,
    NarrationTurnResult,
    NarratorPromptTier,
    NpcMention,
    Orchestrator,
    TurnContext,
    VisualScene,
    extract_structured_from_response,
    run_narration_turn,
)
from sidequest.agents.prompt_framework import (
    AttentionZone,
    PromptComposer,
    PromptRegistry,
    PromptSection,
    RuleTier,
    SectionCategory,
    SoulData,
    SoulPrinciple,
    parse_soul_md,
)

__all__ = [
    # local dm decomposer (Group B)
    "LocalDM",
    # narrator + orchestrator (Phase 1 — story 41-5)
    "NarratorAgent",
    "narrator_output_format_text",
    "ActionRewrite",
    "BeatSelection",
    "NarratorPromptTier",
    "NarrationTurnResult",
    "NpcMention",
    "Orchestrator",
    "TurnContext",
    "VisualScene",
    "extract_structured_from_response",
    "run_narration_turn",
    # agent
    "Agent",
    "AgentResponse",
    "BaseAgent",
    "make_agent",
    # claude_client
    "ClaudeClient",
    "ClaudeClientBuilder",
    "ClaudeClientError",
    "ClaudeResponse",
    "ClaudeTimeoutError",
    "EmptyResponse",
    "LlmCapabilities",
    "LlmClient",
    "LlmClientError",
    "SubprocessFailed",
    # prompt_framework
    "AttentionZone",
    "PromptComposer",
    "PromptRegistry",
    "PromptSection",
    "RuleTier",
    "SectionCategory",
    "SoulData",
    "SoulPrinciple",
    "parse_soul_md",
]

__all__ += [
    "OllamaClient",
    "OllamaClientError",
    "UnknownBackend",
    "build_llm_client",
]
