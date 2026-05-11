"""Tests for PromptRegistry.compose_split — the system/user partition."""

from __future__ import annotations

from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)

AGENT = "narrator"


def _section(name: str, content: str, *, zone: AttentionZone = AttentionZone.Valley) -> PromptSection:
    return PromptSection.new(
        name=name,
        content=content,
        zone=zone,
        category=SectionCategory.State,
    )


def test_stable_section_goes_to_system_bucket():
    """A registered section whose name is on the allowlist appears in system_prompt only."""
    registry = PromptRegistry()
    registry.register_section(AGENT, _section("soul_principles", "soul content"))
    registry.register_section(AGENT, _section("player_action", "player text"))

    system, user = registry.compose_split(AGENT)
    assert "soul content" in system
    assert "soul content" not in user
    assert "player text" in user
    assert "player text" not in system


def test_unknown_section_defaults_to_user_bucket():
    """A section name not on the allowlist appears in user_message only."""
    registry = PromptRegistry()
    registry.register_section(AGENT, _section("some_dynamic_thing", "dynamic content"))

    system, user = registry.compose_split(AGENT)
    assert system == ""
    assert "dynamic content" in user


def test_both_buckets_preserve_zone_order():
    """Within each bucket, sections are emitted in zone order (Primacy → Recency)."""
    registry = PromptRegistry()
    registry.register_section(
        AGENT,
        _section("narrator_identity", "IDENTITY-LATE", zone=AttentionZone.Recency),
    )
    registry.register_section(
        AGENT,
        _section("genre_identity", "IDENTITY-EARLY", zone=AttentionZone.Primacy),
    )

    system, _ = registry.compose_split(AGENT)
    assert system.index("IDENTITY-EARLY") < system.index("IDENTITY-LATE")


def test_empty_agent_returns_empty_pair():
    """compose_split on an unknown agent returns ('', '') without error."""
    registry = PromptRegistry()
    system, user = registry.compose_split(AGENT)
    assert (system, user) == ("", "")
