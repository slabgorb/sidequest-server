"""Tests for agent.py — Agent protocol, AgentResponse, BaseAgent, make_agent.

Port of agent.rs concepts. Wiring test: imports from public sidequest.agents API.
"""

from __future__ import annotations

import pytest

from sidequest.agents import Agent, AgentResponse, BaseAgent, make_agent
from sidequest.agents.agent import BaseAgent
from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.agents.prompt_framework.types import AttentionZone, SectionCategory


# =========================================================================
# AgentResponse
# =========================================================================


def test_agent_response_fields():
    resp = AgentResponse(text="Hello", raw_output='{"result": "Hello"}')
    assert resp.text == "Hello"
    assert resp.raw_output == '{"result": "Hello"}'


def test_agent_response_is_frozen():
    resp = AgentResponse(text="Hello", raw_output="raw")
    with pytest.raises(Exception):
        resp.text = "Mutated"  # type: ignore[misc]


# =========================================================================
# BaseAgent
# =========================================================================


def test_base_agent_name_raises():
    agent = BaseAgent()
    with pytest.raises(NotImplementedError):
        agent.name()


def test_base_agent_system_prompt_raises():
    agent = BaseAgent()
    with pytest.raises(NotImplementedError):
        agent.system_prompt()


# =========================================================================
# make_agent factory
# =========================================================================


def test_make_agent_returns_agent_with_correct_name():
    agent = make_agent("narrator", "You are the narrator.")
    assert agent.name() == "narrator"


def test_make_agent_returns_agent_with_correct_prompt():
    agent = make_agent("narrator", "You are the narrator.")
    assert agent.system_prompt() == "You are the narrator."


def test_make_agent_build_context_registers_identity_section():
    agent = make_agent("narrator", "You are the narrator.")
    registry = PromptRegistry()
    agent.build_context(registry)

    sections = registry.get_sections("narrator", category=SectionCategory.Identity)
    assert len(sections) == 1
    assert sections[0].zone == AttentionZone.Primacy
    assert sections[0].content == "You are the narrator."


def test_make_agent_build_context_section_name_is_agent_identity():
    agent = make_agent("troper", "You are the troper.")
    registry = PromptRegistry()
    agent.build_context(registry)

    sections = registry.registry("troper")
    assert len(sections) == 1
    assert sections[0].name == "troper_identity"


def test_make_agent_satisfies_agent_protocol():
    agent = make_agent("test_agent", "Prompt.")
    assert isinstance(agent, Agent)


# =========================================================================
# Wiring test — imports from public sidequest.agents
# =========================================================================


def test_wiring_import_from_public_api():
    """Verify Agent, BaseAgent, make_agent are importable from sidequest.agents."""
    from sidequest.agents import Agent, BaseAgent, make_agent

    a = make_agent("wiring_test", "Wiring test agent.")
    assert a.name() == "wiring_test"
    assert isinstance(a, Agent)
