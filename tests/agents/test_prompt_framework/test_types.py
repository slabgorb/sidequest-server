"""Tests for prompt_framework/types.py.

Port of sidequest-agents/src/prompt_framework/tests.rs — AttentionZone,
SectionCategory, RuleTier, and PromptSection test blocks.
"""

from __future__ import annotations

import json

import pytest

from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    RuleTier,
    SectionCategory,
)


# =========================================================================
# AttentionZone ordering tests
# =========================================================================


def test_attention_zone_order_primacy_is_first():
    assert AttentionZone.Primacy.order() == 0


def test_attention_zone_order_early_is_second():
    assert AttentionZone.Early.order() == 1


def test_attention_zone_order_valley_is_third():
    assert AttentionZone.Valley.order() == 2


def test_attention_zone_order_late_is_fourth():
    assert AttentionZone.Late.order() == 3


def test_attention_zone_order_recency_is_last():
    assert AttentionZone.Recency.order() == 4


def test_attention_zone_primacy_less_than_early():
    assert AttentionZone.Primacy < AttentionZone.Early


def test_attention_zone_early_less_than_valley():
    assert AttentionZone.Early < AttentionZone.Valley


def test_attention_zone_valley_less_than_late():
    assert AttentionZone.Valley < AttentionZone.Late


def test_attention_zone_late_less_than_recency():
    assert AttentionZone.Late < AttentionZone.Recency


def test_attention_zone_primacy_not_greater_than_recency():
    assert AttentionZone.Primacy < AttentionZone.Recency


def test_attention_zone_same_zone_is_equal():
    assert AttentionZone.Valley == AttentionZone.Valley
    # Same-zone ordering must not strictly precede itself.
    assert AttentionZone.Valley >= AttentionZone.Valley


def test_attention_zone_all_ordered_returns_five_zones():
    zones = AttentionZone.all_ordered()
    assert len(zones) == 5


def test_attention_zone_all_ordered_is_sorted():
    zones = AttentionZone.all_ordered()
    assert zones == [
        AttentionZone.Primacy,
        AttentionZone.Early,
        AttentionZone.Valley,
        AttentionZone.Late,
        AttentionZone.Recency,
    ]


def test_attention_zone_sorting_list_produces_correct_order():
    zones = [
        AttentionZone.Recency,
        AttentionZone.Primacy,
        AttentionZone.Late,
        AttentionZone.Early,
        AttentionZone.Valley,
    ]
    zones.sort()
    assert zones[0] == AttentionZone.Primacy
    assert zones[4] == AttentionZone.Recency


# =========================================================================
# AttentionZone serde tests
# =========================================================================


def test_attention_zone_serializes_to_snake_case():
    assert AttentionZone.Primacy.value == "primacy"


def test_attention_zone_deserializes_from_snake_case():
    zone = AttentionZone("valley")
    assert zone == AttentionZone.Valley


def test_attention_zone_rejects_unknown_value():
    with pytest.raises(ValueError):
        AttentionZone("unknown_zone")


# =========================================================================
# SectionCategory tests
# =========================================================================


def test_section_category_has_nine_variants():
    categories = [
        SectionCategory.Identity,
        SectionCategory.Guardrail,
        SectionCategory.Soul,
        SectionCategory.Genre,
        SectionCategory.State,
        SectionCategory.Action,
        SectionCategory.Format,
        SectionCategory.Context,
        SectionCategory.Role,
    ]
    assert len(categories) == 9
    # All distinct
    for i in range(len(categories)):
        for j in range(i + 1, len(categories)):
            assert categories[i] != categories[j]


def test_section_category_serializes_to_snake_case():
    assert SectionCategory.Guardrail.value == "guardrail"


def test_section_category_roundtrips_through_value():
    original = SectionCategory.Soul
    restored = SectionCategory(original.value)
    assert original == restored


# =========================================================================
# RuleTier tests
# =========================================================================


def test_rule_tier_has_three_variants():
    tiers = [RuleTier.Critical, RuleTier.Firm, RuleTier.Coherence]
    assert len(tiers) == 3
    assert tiers[0] != tiers[1]
    assert tiers[1] != tiers[2]
    assert tiers[0] != tiers[2]


def test_rule_tier_serializes_to_snake_case():
    assert RuleTier.Critical.value == "critical"


def test_rule_tier_roundtrips_through_value():
    for tier in [RuleTier.Critical, RuleTier.Firm, RuleTier.Coherence]:
        restored = RuleTier(tier.value)
        assert tier == restored


# =========================================================================
# PromptSection construction tests
# =========================================================================


def test_prompt_section_new_sets_fields():
    section = PromptSection.new(
        "test_section",
        "You are a narrator.",
        AttentionZone.Primacy,
        SectionCategory.Identity,
    )
    assert section.name == "test_section"
    assert section.category == SectionCategory.Identity
    assert section.zone == AttentionZone.Primacy
    assert section.content == "You are a narrator."
    assert section.source is None


def test_prompt_section_with_source_sets_source():
    section = PromptSection.with_source(
        "soul_principles",
        "Agency: The player controls their character.",
        AttentionZone.Early,
        SectionCategory.Soul,
        "soul_md",
    )
    assert section.source == "soul_md"


def test_prompt_section_token_estimate_counts_words():
    section = PromptSection.new(
        "test",
        "one two three four five",
        AttentionZone.Valley,
        SectionCategory.Genre,
    )
    assert section.token_estimate() == 5


def test_prompt_section_token_estimate_empty_content_is_zero():
    section = PromptSection.new("empty", "", AttentionZone.Late, SectionCategory.State)
    assert section.token_estimate() == 0


def test_prompt_section_is_empty_true_for_empty_content():
    section = PromptSection.new("empty", "", AttentionZone.Late, SectionCategory.State)
    assert section.is_empty()


def test_prompt_section_is_empty_false_for_nonempty_content():
    section = PromptSection.new(
        "notempty",
        "has content",
        AttentionZone.Late,
        SectionCategory.State,
    )
    assert not section.is_empty()


# =========================================================================
# PromptSection serde tests
# =========================================================================


def test_prompt_section_json_roundtrip():
    section = PromptSection.new(
        "genre_tone",
        "Dark and gritty.",
        AttentionZone.Early,
        SectionCategory.Genre,
    )
    dumped = section.model_dump_json()
    restored = PromptSection.model_validate_json(dumped)
    assert section == restored


def test_prompt_section_json_roundtrip_with_source():
    section = PromptSection.with_source(
        "lore",
        "The Flickering Reach is a wasteland.",
        AttentionZone.Valley,
        SectionCategory.Genre,
        "genre_pack",
    )
    dumped = section.model_dump_json()
    restored = PromptSection.model_validate_json(dumped)
    assert section == restored


def test_prompt_section_rejects_unknown_fields():
    bad_json = json.dumps(
        {
            "name": "test",
            "category": "identity",
            "zone": "primacy",
            "content": "hello",
            "bogus_field": "should fail",
        }
    )
    with pytest.raises(Exception):
        PromptSection.model_validate_json(bad_json)


# =========================================================================
# Edge cases and boundary tests
# =========================================================================


def test_prompt_section_whitespace_only_content_is_empty():
    section = PromptSection.new("ws", "   ", AttentionZone.Valley, SectionCategory.State)
    assert section.is_empty()
    assert section.token_estimate() == 0


def test_prompt_section_multiline_content_token_estimate():
    section = PromptSection.new(
        "multi",
        "line one\nline two\nline three",
        AttentionZone.Valley,
        SectionCategory.Genre,
    )
    # "line one line two line three" = 6 words
    assert section.token_estimate() == 6


def test_attention_zone_is_comparable():
    zone = AttentionZone.Primacy
    copy = AttentionZone.Primacy
    assert zone == copy


def test_section_category_is_comparable():
    cat = SectionCategory.Soul
    copy = SectionCategory.Soul
    assert cat == copy


def test_rule_tier_is_comparable():
    tier = RuleTier.Critical
    copy = RuleTier.Critical
    assert tier == copy
