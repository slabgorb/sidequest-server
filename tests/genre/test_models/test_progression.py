"""Tests for progression model types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models import Ability, AffinityTier, ProgressionConfig


class TestAbility:
    def test_full_form(self) -> None:
        a = Ability(name="Strike", experience="You hit hard.", limits="Once per turn.")
        assert a.name == "Strike"

    def test_simple_string_form(self) -> None:
        a = Ability.model_validate("Stonewise")
        assert a.name == "Stonewise"
        assert a.experience == ""
        assert a.limits == ""

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Ability.model_validate({"name": "X", "experience": "Y", "limits": "Z", "extra": True})


class TestAffinityTier:
    def test_valid(self) -> None:
        tier = AffinityTier(
            name="Crawler",
            description="Dungeon veteran",
            abilities=[Ability(name="Stonewise")],
        )
        assert tier.name == "Crawler"
        assert len(tier.abilities) == 1

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AffinityTier.model_validate({
                "name": "T", "description": "D", "abilities": [], "unknown": True,
            })


class TestProgressionConfig:
    def test_defaults(self) -> None:
        p = ProgressionConfig()
        assert p.affinities == []
        assert p.max_level == 0

    def test_roundtrip(self) -> None:
        p = ProgressionConfig(max_level=10, milestones_per_level=3)
        data = p.model_dump()
        p2 = ProgressionConfig.model_validate(data)
        assert p2.max_level == 10
        assert p2.milestones_per_level == 3

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ProgressionConfig.model_validate({"affinities": [], "bogus": True})
