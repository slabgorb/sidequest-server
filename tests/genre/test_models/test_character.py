"""Tests for character model types."""

from __future__ import annotations

import pytest

from sidequest.genre.models import (
    MechanicalEffects,
    NpcArchetype,
    VisualStyle,
)


class TestNpcArchetype:
    def test_extra_allowed(self) -> None:
        """NpcArchetype allows extra fields (genre packs add role, morale, etc.)"""
        a = NpcArchetype.model_validate({
            "name": "Merchant",
            "description": "Sells things",
            "personality_traits": ["greedy"],
            "typical_classes": ["Trader"],
            "typical_races": ["Human"],
            "stat_ranges": {"CHA": [8, 16]},
            "inventory_hints": ["ledger"],
            "dialogue_quirks": ["counts coins"],
            "disposition_default": 5,
            "role": "merchant",  # genre-specific extra
            "morale": 7,  # genre-specific extra
        })
        assert a.name == "Merchant"


class TestMechanicalEffects:
    def test_defaults(self) -> None:
        me = MechanicalEffects()
        assert me.class_hint is None
        assert me.stat_bonuses == {}

    def test_catch_alias(self) -> None:
        """'catch' YAML key maps to catch_phrase field."""
        me = MechanicalEffects.model_validate({"catch": "My war-cry!"})
        assert me.catch_phrase == "My war-cry!"

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            MechanicalEffects.model_validate({"bogus_field": True})


class TestVisualStyle:
    def test_extra_allowed(self) -> None:
        """VisualStyle accepts extra genre-specific fields."""
        vs = VisualStyle.model_validate({
            "positive_suffix": "grim",
            "negative_prompt": "bright",
            "preferred_model": "flux",
            "base_seed": 0,
            "extra_field": "ignored",
        })
        assert vs.positive_suffix == "grim"


class TestVisualStyleLoraFieldsRemoved:
    """Story 43-1: LoRA fields are dead code per ADR-070 (Z-Image Turbo).

    These tests fail until VisualStyle drops `lora`, `lora_trigger`,
    `lora_scale`, and `lora_path` from its declared schema. They will
    remain failing during RED phase and pass only after Dev removes the
    fields and the corresponding `_validate_lora_scale` validator.
    """

    def test_lora_field_not_declared(self) -> None:
        assert "lora" not in VisualStyle.model_fields, (
            "VisualStyle.lora must be removed per ADR-070 supersession of ADRs 032/083/084"
        )

    def test_lora_trigger_field_not_declared(self) -> None:
        assert "lora_trigger" not in VisualStyle.model_fields, (
            "VisualStyle.lora_trigger must be removed per ADR-070"
        )

    def test_lora_scale_field_not_declared(self) -> None:
        assert "lora_scale" not in VisualStyle.model_fields, (
            "VisualStyle.lora_scale must be removed per ADR-070"
        )

    def test_lora_path_field_not_declared(self) -> None:
        # lora_path is named in the story scope even though it never
        # actually shipped on VisualStyle — guarding against re-introduction.
        assert "lora_path" not in VisualStyle.model_fields, (
            "VisualStyle.lora_path must not be (re-)introduced — Z-Image text-only path per ADR-070"
        )

    def test_lora_scale_validator_removed(self) -> None:
        """The _validate_lora_scale class method must go with the field.

        Pydantic raises at class definition time if a @field_validator
        references a missing field, so a stale validator would prevent
        VisualStyle from importing at all. We assert explicitly so the
        failure mode is named, not just an import crash.
        """
        assert not hasattr(VisualStyle, "_validate_lora_scale"), (
            "Remove the _validate_lora_scale validator alongside the lora_scale field"
        )

    def test_extra_lora_keys_in_yaml_still_load(self) -> None:
        """Backwards compat: VisualStyle's `extra='allow'` config must
        survive, so legacy genre pack YAMLs that still mention `lora:`
        keep loading without raising. (Story 43-4 will scrub the YAMLs;
        43-1 just removes the typed fields, preserving tolerant loading.)
        """
        vs = VisualStyle.model_validate({
            "positive_suffix": "x",
            "negative_prompt": "y",
            "preferred_model": "flux",
            "base_seed": 0,
            "lora": "legacy.safetensors",
            "lora_trigger": "legacy_trigger",
            "lora_scale": 0.8,
        })
        assert vs.positive_suffix == "x"
        # extra='allow' keeps the unknown keys in __pydantic_extra__,
        # but they are NOT typed fields on the model.
        assert "lora" not in type(vs).model_fields
        assert "lora_trigger" not in type(vs).model_fields
        assert "lora_scale" not in type(vs).model_fields
