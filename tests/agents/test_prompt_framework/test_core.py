"""Tests for prompt_framework/core.py — PromptComposer protocol and PromptRegistry.

Port of sidequest-agents/src/prompt_framework/tests.rs — PromptComposer
trait test blocks, adapted for Python PromptRegistry.
"""

from __future__ import annotations

from sidequest.agents.prompt_framework.core import PromptComposer, PromptRegistry
from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)

# =========================================================================
# PromptRegistry — sorting by zone order
# =========================================================================


def test_registry_sections_are_ordered_by_zone_in_compose():
    """compose() sorts by zone even if sections were registered out of order."""
    registry = PromptRegistry()

    # Register out of order.
    registry.register_section(
        "narrator",
        PromptSection.new(
            "checklist", "Check rules.", AttentionZone.Recency, SectionCategory.Guardrail
        ),
    )
    registry.register_section(
        "narrator",
        PromptSection.new(
            "identity", "You are a narrator.", AttentionZone.Primacy, SectionCategory.Identity
        ),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("lore", "World lore.", AttentionZone.Valley, SectionCategory.Genre),
    )

    composed = registry.compose("narrator")
    # identity (Primacy) should appear before lore (Valley) before checklist (Recency)
    assert composed.index("You are a narrator.") < composed.index("World lore.")
    assert composed.index("World lore.") < composed.index("Check rules.")


def test_registry_preserves_insertion_order_within_zone():
    registry = PromptRegistry()

    registry.register_section(
        "narrator",
        PromptSection.new("first_early", "Soul.", AttentionZone.Early, SectionCategory.Soul),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("second_early", "Genre.", AttentionZone.Early, SectionCategory.Genre),
    )

    sections = registry.registry("narrator")
    assert sections[0].name == "first_early"
    assert sections[1].name == "second_early"


def test_registry_get_sections_filters_by_category():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("identity", "Id.", AttentionZone.Primacy, SectionCategory.Identity),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("soul", "Soul.", AttentionZone.Early, SectionCategory.Soul),
    )

    soul_sections = registry.get_sections("narrator", category=SectionCategory.Soul)
    assert len(soul_sections) == 1
    assert soul_sections[0].name == "soul"


def test_registry_get_sections_filters_by_zone():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("a", "A.", AttentionZone.Primacy, SectionCategory.Identity),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("b", "B.", AttentionZone.Early, SectionCategory.Genre),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("c", "C.", AttentionZone.Early, SectionCategory.State),
    )

    early = registry.get_sections("narrator", zone=AttentionZone.Early)
    assert len(early) == 2


def test_registry_get_sections_filters_by_both():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("a", "A.", AttentionZone.Early, SectionCategory.Genre),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("b", "B.", AttentionZone.Early, SectionCategory.State),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("c", "C.", AttentionZone.Valley, SectionCategory.Genre),
    )

    result = registry.get_sections(
        "narrator",
        category=SectionCategory.Genre,
        zone=AttentionZone.Early,
    )
    assert len(result) == 1
    assert result[0].name == "a"


def test_registry_clear_removes_all_sections():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("x", "X.", AttentionZone.Primacy, SectionCategory.Identity),
    )
    registry.clear("narrator")
    assert registry.registry("narrator") == []


def test_registry_compose_joins_sections():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new(
            "identity", "You are a narrator.", AttentionZone.Primacy, SectionCategory.Identity
        ),
    )
    output = registry.compose("narrator")
    assert "You are a narrator." in output


def test_registry_empty_agent_returns_empty_string():
    registry = PromptRegistry()
    assert registry.compose("nonexistent") == ""


def test_registry_multiple_agents_are_independent():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("n1", "Narrator.", AttentionZone.Primacy, SectionCategory.Identity),
    )
    registry.register_section(
        "combat",
        PromptSection.new("c1", "Combat.", AttentionZone.Primacy, SectionCategory.Identity),
    )

    assert len(registry.registry("narrator")) == 1
    assert len(registry.registry("combat")) == 1
    assert registry.registry("narrator")[0].content == "Narrator."
    assert registry.registry("combat")[0].content == "Combat."


def test_registry_skips_empty_sections_in_compose():
    registry = PromptRegistry()
    registry.register_section(
        "narrator",
        PromptSection.new("empty", "", AttentionZone.Primacy, SectionCategory.Identity),
    )
    registry.register_section(
        "narrator",
        PromptSection.new("content", "real content", AttentionZone.Early, SectionCategory.Soul),
    )
    output = registry.compose("narrator")
    assert output == "real content"


# =========================================================================
# PromptRegistry helper methods
# =========================================================================


def test_register_pacing_section_narrator_receives_section():
    registry = PromptRegistry()
    registry.register_pacing_section("narrator", "Keep tension high.")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert len(sections) == 1
    assert "Keep tension high." in sections[0].content


def test_register_pacing_section_non_narrator_ignored():
    registry = PromptRegistry()
    registry.register_pacing_section("troper", "Keep tension high.")
    assert registry.registry("troper") == []


def test_register_pacing_section_includes_escalation_beat():
    registry = PromptRegistry()
    registry.register_pacing_section("narrator", "Slow down.", escalation_beat="The villain emerges.")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert len(sections) == 1
    assert "The villain emerges." in sections[0].content


def test_register_verbosity_concise():
    registry = PromptRegistry()
    registry.register_verbosity_section("narrator", "concise")
    sections = registry.get_sections("narrator", zone=AttentionZone.Recency)
    assert len(sections) == 1
    assert "400 characters" in sections[0].content


def test_register_verbosity_standard():
    registry = PromptRegistry()
    registry.register_verbosity_section("narrator", "standard")
    sections = registry.get_sections("narrator", zone=AttentionZone.Recency)
    assert len(sections) == 1
    assert "600 characters" in sections[0].content


def test_register_verbosity_verbose():
    registry = PromptRegistry()
    registry.register_verbosity_section("narrator", "verbose")
    sections = registry.get_sections("narrator", zone=AttentionZone.Recency)
    assert len(sections) == 1
    assert "1000 characters" in sections[0].content


def test_register_verbosity_unknown_falls_back_to_standard():
    registry = PromptRegistry()
    registry.register_verbosity_section("narrator", "banana")
    sections = registry.get_sections("narrator", zone=AttentionZone.Recency)
    assert len(sections) == 1
    assert "600 characters" in sections[0].content


def test_register_verbosity_non_narrator_ignored():
    registry = PromptRegistry()
    registry.register_verbosity_section("troper", "concise")
    assert registry.registry("troper") == []


def test_register_vocabulary_accessible():
    registry = PromptRegistry()
    registry.register_vocabulary_section("narrator", "accessible")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert len(sections) == 1
    assert "simple, direct language" in sections[0].content


def test_register_vocabulary_epic():
    registry = PromptRegistry()
    registry.register_vocabulary_section("narrator", "epic")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert len(sections) == 1
    assert "archaic" in sections[0].content


def test_register_vocabulary_default_is_literary():
    registry = PromptRegistry()
    registry.register_vocabulary_section("narrator", "literary")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert len(sections) == 1
    assert "clear prose" in sections[0].content


def test_register_vocabulary_non_narrator_ignored():
    registry = PromptRegistry()
    registry.register_vocabulary_section("troper", "epic")
    assert registry.registry("troper") == []


def test_register_footnote_protocol_section():
    registry = PromptRegistry()
    registry.register_footnote_protocol_section("narrator")
    sections = registry.get_sections("narrator", zone=AttentionZone.Late)
    assert any("FOOTNOTE PROTOCOL" in s.content for s in sections)


def test_register_resource_section_empty_declarations_no_section():
    registry = PromptRegistry()
    registry.register_resource_section("narrator", [], {})
    assert registry.registry("narrator") == []


def test_register_resource_section_with_declarations():
    registry = PromptRegistry()
    decls = [
        {"name": "fuel", "label": "Fuel", "starting": 10.0, "max": 10.0, "voluntary": True, "decay_per_turn": 0.0},
    ]
    registry.register_resource_section("narrator", decls, {"fuel": 7.5})
    sections = registry.get_sections("narrator", zone=AttentionZone.Valley)
    assert len(sections) == 1
    assert "Fuel" in sections[0].content
    assert "7.5" in sections[0].content


def test_register_resource_section_decay_shown():
    registry = PromptRegistry()
    decls = [
        {"name": "hope", "label": "Hope", "starting": 100.0, "max": 100.0, "voluntary": False, "decay_per_turn": 2.5},
    ]
    registry.register_resource_section("narrator", decls, {})
    sections = registry.get_sections("narrator", zone=AttentionZone.Valley)
    assert "decay 2.5/turn" in sections[0].content


# =========================================================================
# PromptComposer protocol conformance
# =========================================================================


def test_prompt_registry_satisfies_composer_protocol():
    registry = PromptRegistry()
    assert isinstance(registry, PromptComposer)
