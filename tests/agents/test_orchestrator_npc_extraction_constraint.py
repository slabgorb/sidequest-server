"""Story 49-2 — Recency-zone ``npc_extraction_constraint`` section.

AC4 — paired with the server-side auto-minter (49-2 main fix). The
auto-mint catches the failure POST-hoc; this Recency-zone Guardrail
catches it AT NARRATION TIME by restating the extraction rule in the
high-attention zone where the narrator is composing turn N's prose.

The 2026-05-11 Glenross scene that motivates both halves: narrator wrote
dialogue about Father in detail but emitted ``npcs_present`` covering
only Reverend Murchison + the pinafore girl. The narrator KNOWS the
extraction rule — it lives in the System-zone schema block — but System
attention has decayed by turn 20+. ADR-009 (attention-aware prompt
zones) prescribes restating load-bearing rules in Recency as a Guardrail
section. This is the same pattern as ``npc_intro_visual_constraint``
and ``confrontation_trigger_constraint``.

These tests pin the section's registration in
``Orchestrator.build_narrator_prompt``: present on every turn, in
Recency zone, categorized as Guardrail, with the AC4 required content
phrases (role-named, patients/parents/children/siblings, MUST appear
in npcs_present).
"""

from __future__ import annotations

import pytest

from sidequest.agents.orchestrator import Orchestrator
from sidequest.agents.prompt_framework.types import AttentionZone, SectionCategory


def _section_by_name(registry, agent_name: str, name: str):
    """Return the registered PromptSection with the given name, or None."""
    for section in registry.registry(agent_name):
        if section.name == name:
            return section
    return None


# ---------------------------------------------------------------------------
# Section registration / zone / category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_extraction_constraint_section_is_registered(
    simple_turn_context_turn_three,
):
    """AC4: orchestrator MUST register a ``npc_extraction_constraint``
    PromptSection on every narrator turn. The constraint runs every turn
    (Delta-tier inclusive) — the narrator can forget to extract on any
    turn, not just opening / Full-tier prompts.
    """
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt(
        "I lean over the wounded man.", simple_turn_context_turn_three
    )
    section = _section_by_name(
        registry, orch._narrator.name(), "npc_extraction_constraint"
    )
    assert section is not None, (
        "npc_extraction_constraint section was not registered. "
        "Existing Recency-zone neighbours: "
        f"{[s.name for s in registry.registry(orch._narrator.name()) if s.zone == AttentionZone.Recency]}"
    )


@pytest.mark.asyncio
async def test_npc_extraction_constraint_is_in_recency_zone(
    simple_turn_context_turn_three,
):
    """The whole point of the AC4 section: must land in
    ``AttentionZone.Recency``, not Valley/Late. Valley placement would
    re-create the attention-decay failure mode that caused the Glenross
    miss in the first place (ADR-009).
    """
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt(
        "act", simple_turn_context_turn_three
    )
    section = _section_by_name(
        registry, orch._narrator.name(), "npc_extraction_constraint"
    )
    assert section is not None
    assert section.zone == AttentionZone.Recency, (
        f"npc_extraction_constraint registered in {section.zone}, "
        "must be Recency to ride high-attention with player_action and "
        "the sibling extraction-related guardrails."
    )


@pytest.mark.asyncio
async def test_npc_extraction_constraint_category_is_guardrail(
    simple_turn_context_turn_three,
):
    """The section is a behavior rule (must extract role-named individuals),
    not state. ``SectionCategory.Guardrail`` parallels the sibling
    ``npc_intro_visual_constraint`` and ``confrontation_trigger_constraint``.
    """
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt(
        "act", simple_turn_context_turn_three
    )
    section = _section_by_name(
        registry, orch._narrator.name(), "npc_extraction_constraint"
    )
    assert section is not None
    assert section.category == SectionCategory.Guardrail, (
        f"category must be Guardrail (got {section.category}) — rules "
        "about the narrator's emission obligations are guardrails."
    )


# ---------------------------------------------------------------------------
# Section content — restates the rule the AC requires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_npc_extraction_constraint_content_mentions_role_named(
    simple_turn_context_turn_three,
):
    """AC4 body MUST include the phrase 'role-named' and enumerate the
    specific NPC types the Glenross scene missed (patients, parents,
    children, siblings, recurring townsfolk). Without these concrete
    nouns the narrator's attention bounces off an abstract rule.
    """
    orch = Orchestrator()
    prompt, _ = await orch.build_narrator_prompt(
        "act", simple_turn_context_turn_three
    )
    # AC4 verbatim: 'Any person named or role-named in this turn's prose'
    assert "role-named" in prompt, (
        "AC4 requires the constraint to use the exact phrase 'role-named' "
        "so the narrator's pattern-matcher engages on the high-precision "
        "vocabulary it knows from the schema block."
    )
    # Concrete NPC type enumeration per AC4
    for noun in ("patients", "parents", "children", "siblings"):
        assert noun in prompt, (
            f"AC4 requires concrete enumeration including {noun!r} — "
            "the Glenross miss was specifically a parent (Father) that "
            "lived as a 'patient' in the scene, so both nouns must "
            "appear in the constraint."
        )


@pytest.mark.asyncio
async def test_npc_extraction_constraint_content_demands_npcs_present(
    simple_turn_context_turn_three,
):
    """The constraint must explicitly demand the missing names appear in
    ``npcs_present``. Vague rules ('include all NPCs') drift; AC4 pins
    the exact field name to constrain the structured-emission block.
    """
    orch = Orchestrator()
    prompt, _ = await orch.build_narrator_prompt(
        "act", simple_turn_context_turn_three
    )
    assert "npcs_present" in prompt, (
        "AC4 constraint must name 'npcs_present' explicitly so the "
        "narrator's JSON-block generator knows which field to populate."
    )
    # Must convey 'MUST', not 'should' — this is a hard obligation per AC4.
    assert "MUST" in prompt, (
        "AC4 verbatim uses 'MUST' for the obligation. Soft language "
        "('should', 'consider') would let the narrator skip extraction "
        "on busy turns, which is exactly the regression mode."
    )


@pytest.mark.asyncio
async def test_npc_extraction_constraint_present_on_delta_tier(
    simple_turn_context_turn_three,
):
    """Parallel to ``test_confrontation_trigger_constraint_present_on_delta_tier``:
    the constraint must ride every turn, including the Delta-tier
    (mid-session, post-opening). The Glenross miss happened on a mid-
    session turn — Delta presence is the load-bearing case.
    """
    orch = Orchestrator()
    prompt, _ = await orch.build_narrator_prompt(
        "act", simple_turn_context_turn_three
    )
    # The Recency-zone Guardrail must appear regardless of tier. Use the
    # rule-marker text as the existence check.
    assert "role-named" in prompt
    assert "npcs_present" in prompt
