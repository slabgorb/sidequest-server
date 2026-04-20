"""sidequest.agents — Claude CLI agent foundation.

Port of sidequest-agents crate (Phase 1 subset).
ADR-082: Python server narration vertical slice.

Phase 1 exports:
- Agent (Protocol), AgentResponse, BaseAgent, make_agent
- ClaudeClient, ClaudeLike, ClaudeClientBuilder, ClaudeResponse
- ClaudeClientError, TimeoutError, SubprocessFailed, EmptyResponse
- PromptComposer, PromptRegistry, PromptSection
- AttentionZone, SectionCategory, RuleTier
- SoulData, SoulPrinciple, parse_soul_md
- PreprocessorError hierarchy + preprocess_action, preprocess_action_with_client

Phase 2+ (orchestrator, narrator, context_builder) are in subagent B scope.
"""

from __future__ import annotations

from sidequest.agents.agent import Agent, AgentResponse, BaseAgent, make_agent
from sidequest.agents.claude_client import (
    ClaudeClient,
    ClaudeClientBuilder,
    ClaudeClientError,
    ClaudeLike,
    ClaudeResponse,
    EmptyResponse,
    SubprocessFailed,
)
from sidequest.agents.claude_client import TimeoutError as ClaudeTimeoutError
from sidequest.agents.preprocessor import (
    LlmFailed,
    OutputTooLong,
    ParseFailed,
    PreprocessError,
    build_prompt,
    parse_response,
    preprocess_action,
    preprocess_action_with_client,
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
    # agent
    "Agent",
    "AgentResponse",
    "BaseAgent",
    "make_agent",
    # claude_client
    "ClaudeClient",
    "ClaudeClientBuilder",
    "ClaudeClientError",
    "ClaudeLike",
    "ClaudeResponse",
    "ClaudeTimeoutError",
    "EmptyResponse",
    "SubprocessFailed",
    # preprocessor
    "LlmFailed",
    "OutputTooLong",
    "ParseFailed",
    "PreprocessError",
    "build_prompt",
    "parse_response",
    "preprocess_action",
    "preprocess_action_with_client",
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
