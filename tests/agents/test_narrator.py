"""Tests for sidequest/agents/narrator.py.

Port of sidequest-agents/src/agents/narrator.rs tests.
All assertions are against prompt structure, not LLM output.
No live Claude CLI calls.
"""

from __future__ import annotations

import pytest

from sidequest.agents.narrator import (
    NARRATOR_AGENCY,
    NARRATOR_COMBAT_RULES,
    NARRATOR_CHASE_RULES,
    NARRATOR_CONSTRAINTS,
    NARRATOR_CONSEQUENCES,
    NARRATOR_DIALOGUE_RULES,
    NARRATOR_IDENTITY,
    NARRATOR_OUTPUT_ONLY,
    NARRATOR_OUTPUT_STYLE,
    NARRATOR_REFERRAL_RULE,
    NarratorAgent,
    narrator_output_format_text,
)
from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.agents.prompt_framework.types import AttentionZone, SectionCategory


# ---------------------------------------------------------------------------
# NarratorAgent — construction
# ---------------------------------------------------------------------------


def test_narrator_agent_name():
    agent = NarratorAgent()
    assert agent.name() == "narrator"


def test_narrator_agent_system_prompt_is_identity():
    agent = NarratorAgent()
    assert agent.system_prompt() == NARRATOR_IDENTITY


def test_narrator_output_format_text_matches_constant():
    assert narrator_output_format_text() == NARRATOR_OUTPUT_ONLY


# ---------------------------------------------------------------------------
# NarratorAgent.build_context — section registration
# ---------------------------------------------------------------------------


def _build_registry(agent: NarratorAgent) -> PromptRegistry:
    registry = PromptRegistry()
    agent.build_context(registry)
    return registry


def test_build_context_registers_identity_section():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.registry("narrator")
    names = [s.name for s in sections]
    assert "narrator_identity" in names


def test_build_context_identity_in_primacy_zone():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    identity_sections = registry.get_sections(
        "narrator", zone=AttentionZone.Primacy, category=SectionCategory.Identity
    )
    assert any(s.name == "narrator_identity" for s in identity_sections)


def test_build_context_registers_constraints_guardrail():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.get_sections(
        "narrator", zone=AttentionZone.Primacy, category=SectionCategory.Guardrail
    )
    names = [s.name for s in sections]
    assert "narrator_constraints" in names


def test_build_context_registers_agency_guardrail():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.get_sections(
        "narrator", zone=AttentionZone.Primacy, category=SectionCategory.Guardrail
    )
    names = [s.name for s in sections]
    assert "narrator_agency" in names


def test_build_context_registers_consequences_guardrail():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.get_sections(
        "narrator", zone=AttentionZone.Primacy, category=SectionCategory.Guardrail
    )
    names = [s.name for s in sections]
    assert "narrator_consequences" in names


def test_build_context_registers_output_style_in_early_zone():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.get_sections("narrator", zone=AttentionZone.Early)
    names = [s.name for s in sections]
    assert "narrator_output_style" in names


def test_build_context_registers_referral_rule_in_early_zone():
    agent = NarratorAgent()
    registry = _build_registry(agent)
    sections = registry.get_sections("narrator", zone=AttentionZone.Early)
    names = [s.name for s in sections]
    assert "narrator_referral_rule" in names


def test_build_context_does_not_register_output_only():
    """narrator_output_only is injected by build_output_format, not build_context."""
    agent = NarratorAgent()
    registry = _build_registry(agent)
    names = [s.name for s in registry.registry("narrator")]
    assert "narrator_output_only" not in names


# ---------------------------------------------------------------------------
# NarratorAgent.build_output_format
# ---------------------------------------------------------------------------


def test_build_output_format_registers_section():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_output_format(registry)
    names = [s.name for s in registry.registry("narrator")]
    assert "narrator_output_only" in names


def test_build_output_format_in_primacy_zone():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_output_format(registry)
    sections = registry.get_sections(
        "narrator", zone=AttentionZone.Primacy, category=SectionCategory.Guardrail
    )
    assert any(s.name == "narrator_output_only" for s in sections)


def test_build_output_format_content_contains_game_patch():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_output_format(registry)
    section = next(
        s for s in registry.registry("narrator") if s.name == "narrator_output_only"
    )
    assert "game_patch" in section.content


# ---------------------------------------------------------------------------
# NarratorAgent.build_encounter_context
# ---------------------------------------------------------------------------


def test_build_encounter_context_registers_section():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_encounter_context(registry)
    names = [s.name for s in registry.registry("narrator")]
    assert "narrator_encounter_rules" in names


def test_build_encounter_context_contains_combat_rules():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_encounter_context(registry)
    section = next(
        s for s in registry.registry("narrator") if s.name == "narrator_encounter_rules"
    )
    assert "COMBAT NARRATION RULES" in section.content


def test_build_encounter_context_contains_chase_rules():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_encounter_context(registry)
    section = next(
        s for s in registry.registry("narrator") if s.name == "narrator_encounter_rules"
    )
    assert "CHASE NARRATION RULES" in section.content


def test_build_encounter_context_in_early_zone():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_encounter_context(registry)
    sections = registry.get_sections("narrator", zone=AttentionZone.Early)
    names = [s.name for s in sections]
    assert "narrator_encounter_rules" in names


# ---------------------------------------------------------------------------
# NarratorAgent.build_dialogue_context
# ---------------------------------------------------------------------------


def test_build_dialogue_context_registers_section():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_dialogue_context(registry)
    names = [s.name for s in registry.registry("narrator")]
    assert "narrator_dialogue_rules" in names


def test_build_dialogue_context_content_contains_dialogue_rules():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_dialogue_context(registry)
    section = next(
        s for s in registry.registry("narrator") if s.name == "narrator_dialogue_rules"
    )
    assert "DIALOGUE NARRATION RULES" in section.content


def test_build_dialogue_context_in_early_zone():
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_dialogue_context(registry)
    sections = registry.get_sections("narrator", zone=AttentionZone.Early)
    names = [s.name for s in sections]
    assert "narrator_dialogue_rules" in names


# ---------------------------------------------------------------------------
# Composed prompt ordering
# ---------------------------------------------------------------------------


def test_compose_with_output_format_has_game_patch_before_player_action():
    """Output format section (Primacy) must appear before player action (Recency)."""
    agent = NarratorAgent()
    registry = PromptRegistry()
    agent.build_context(registry)
    agent.build_output_format(registry)
    registry.register_section(
        "narrator",
        __import__(
            "sidequest.agents.prompt_framework.types",
            fromlist=["PromptSection"],
        ).PromptSection.new(
            "player_action",
            "Player says: look around",
            AttentionZone.Recency,
            SectionCategory.Action,
        ),
    )
    composed = registry.compose("narrator")
    game_patch_pos = composed.find("game_patch")
    player_action_pos = composed.find("Player says: look around")
    assert game_patch_pos < player_action_pos


def test_build_context_wrong_type_raises():
    agent = NarratorAgent()
    with pytest.raises(TypeError, match="Expected PromptRegistry"):
        agent.build_context("not a registry")  # type: ignore[arg-type]


def test_build_output_format_wrong_type_raises():
    agent = NarratorAgent()
    with pytest.raises(TypeError, match="Expected PromptRegistry"):
        agent.build_output_format("not a registry")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Prompt constant content (spot checks for porting correctness)
# ---------------------------------------------------------------------------


def test_narrator_identity_is_gm_of_collaborative_rpg():
    assert "Game Master" in NARRATOR_IDENTITY
    assert "collaborative RPG" in NARRATOR_IDENTITY


def test_narrator_constraints_mentions_never_acknowledge():
    assert "NEVER acknowledge" in NARRATOR_CONSTRAINTS


def test_narrator_agency_mentions_player_controls():
    assert "player controls" in NARRATOR_AGENCY


def test_narrator_output_style_mentions_brevity():
    assert "BREVITY" in NARRATOR_OUTPUT_STYLE


def test_narrator_referral_rule_mentions_never_send_back():
    assert "NEVER send the player back" in NARRATOR_REFERRAL_RULE


def test_narrator_combat_rules_mentions_beat_selections():
    assert "beat_selections" in NARRATOR_COMBAT_RULES


def test_narrator_chase_rules_mentions_beat_selections():
    assert "beat_selections" in NARRATOR_CHASE_RULES


def test_narrator_dialogue_rules_mentions_npc_talk_only():
    assert "NEVER speak for the player character" in NARRATOR_DIALOGUE_RULES


# ---------------------------------------------------------------------------
# action_flags removal (dead-code demolition)
# ---------------------------------------------------------------------------


def test_narrator_output_format_does_not_contain_action_flags_token():
    """action_flags is write-only — never read by server/UI/daemon.
    This test ensures it's been removed from the prompt."""
    assert "action_flags" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'action_flags' token"


def test_narrator_output_format_does_not_contain_is_power_grab():
    """is_power_grab is a dead action_flags field."""
    assert "is_power_grab" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'is_power_grab'"


def test_narrator_output_format_does_not_contain_references_inventory():
    """references_inventory is a dead action_flags field."""
    assert "references_inventory" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'references_inventory'"


def test_narrator_output_format_does_not_contain_references_npc():
    """references_npc is a dead action_flags field."""
    assert "references_npc" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'references_npc'"


def test_narrator_output_format_does_not_contain_references_ability():
    """references_ability is a dead action_flags field."""
    assert "references_ability" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'references_ability'"


def test_narrator_output_format_does_not_contain_references_location():
    """references_location is a dead action_flags field."""
    assert "references_location" not in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must not contain 'references_location'"


def test_narrator_output_format_keeps_action_rewrite():
    """action_rewrite is live — must remain in prompt."""
    assert "action_rewrite" in NARRATOR_OUTPUT_ONLY, \
        "NARRATOR_OUTPUT_ONLY must still contain 'action_rewrite' (it's live)"
