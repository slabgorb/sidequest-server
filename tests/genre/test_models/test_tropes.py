"""Tests for trope model types."""

from __future__ import annotations

import pytest

from sidequest.genre.models import PassiveProgression, TropeDefinition, TropeEscalation


class TestTropeDefinition:
    def test_minimal(self) -> None:
        t = TropeDefinition(name="Test Trope")
        assert t.name == "Test Trope"
        assert t.is_abstract is False
        assert t.category == ""

    def test_abstract_alias(self) -> None:
        """'abstract' YAML key maps to is_abstract field."""
        t = TropeDefinition.model_validate({"name": "Abstract", "abstract": True})
        assert t.is_abstract is True

    def test_escalation(self) -> None:
        t = TropeDefinition(
            name="Tension",
            escalation=[TropeEscalation(at=0.5, event="Something happens")],
        )
        assert len(t.escalation) == 1
        assert t.escalation[0].at == 0.5

    def test_passive_progression(self) -> None:
        pp = PassiveProgression(rate_per_turn=0.02, accelerators=["combat"])
        t = TropeDefinition(name="Active", passive_progression=pp)
        assert t.passive_progression is not None
        assert t.passive_progression.rate_per_turn == pytest.approx(0.02)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            TropeDefinition.model_validate({"name": "T", "bogus_field": True})

    def test_roundtrip(self) -> None:
        t = TropeDefinition(name="T", category="conflict", tension_level=0.7)
        data = t.model_dump()
        t2 = TropeDefinition.model_validate(data)
        assert t2.tension_level == pytest.approx(0.7)
