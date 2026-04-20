"""Agent base protocol and response types.

Port of sidequest-agents/src/agent.rs.

All agents implement the Agent protocol, providing a consistent interface
for the orchestrator. The define_agent macro becomes a factory helper function.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)


class AgentResponse(BaseModel):
    """Response from an agent execution."""

    model_config = {"frozen": True}

    text: str
    raw_output: str


@runtime_checkable
class Agent(Protocol):
    """Protocol defining the agent interface.

    All game agents (Narrator, Combat, NPC, etc.) implement this protocol
    to provide a consistent interface for the orchestrator.
    Port of the Rust Agent trait.
    """

    def name(self) -> str:
        """Agent's display name."""
        ...

    def system_prompt(self) -> str:
        """The system prompt for this agent."""
        ...

    def build_context(self, registry: object) -> None:
        """Add this agent's sections to a PromptRegistry.

        Default implementation wraps system_prompt() as a Primacy/Identity section.
        Agents can override for more granular section composition.

        The registry parameter is typed as object to avoid circular imports at
        the protocol definition level — callers pass a PromptRegistry instance.
        """
        ...


class BaseAgent:
    """Concrete base class that implements Agent with default build_context.

    Analogous to the define_agent! macro's default implementation.
    Subclass and override name() / system_prompt() as needed.
    """

    def name(self) -> str:
        raise NotImplementedError

    def system_prompt(self) -> str:
        raise NotImplementedError

    def build_context(self, registry: object) -> None:
        """Default: wrap system_prompt() as a Primacy/Identity section."""
        # Import here to avoid circular import at module level.
        from sidequest.agents.prompt_framework.core import PromptRegistry

        if not isinstance(registry, PromptRegistry):
            raise TypeError(f"Expected PromptRegistry, got {type(registry)}")
        registry.register_section(
            self.name(),
            PromptSection.new(
                f"{self.name()}_identity",
                self.system_prompt(),
                AttentionZone.Primacy,
                SectionCategory.Identity,
            ),
        )


def make_agent(agent_name: str, prompt_text: str) -> BaseAgent:
    """Factory helper — equivalent to the Rust define_agent! macro.

    Creates a BaseAgent subclass with the given name and system prompt.
    """

    class _ConcreteAgent(BaseAgent):
        def name(self) -> str:
            return agent_name

        def system_prompt(self) -> str:
            return prompt_text

    _ConcreteAgent.__name__ = f"Agent_{agent_name}"
    _ConcreteAgent.__qualname__ = f"Agent_{agent_name}"
    return _ConcreteAgent()
