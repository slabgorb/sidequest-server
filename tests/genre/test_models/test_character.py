"""Tests for character model types."""

from __future__ import annotations

import pytest

from sidequest.genre.models import (
    CharCreationScene,
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
    def test_lora_scale_validation(self) -> None:
        vs = VisualStyle(
            positive_suffix="dungeon style",
            negative_prompt="blur",
            preferred_model="flux",
            base_seed=42,
            lora_scale=1.5,
        )
        assert vs.lora_scale == pytest.approx(1.5)

    def test_lora_scale_rejects_above_2(self) -> None:
        with pytest.raises(Exception, match="<= 2.0"):
            VisualStyle(
                positive_suffix="s", negative_prompt="n",
                preferred_model="m", base_seed=0, lora_scale=3.0,
            )

    def test_lora_scale_rejects_negative(self) -> None:
        with pytest.raises(Exception):
            VisualStyle(
                positive_suffix="s", negative_prompt="n",
                preferred_model="m", base_seed=0, lora_scale=-1.0,
            )

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
