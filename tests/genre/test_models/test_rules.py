"""Tests for rules model types."""

from __future__ import annotations

import pytest

from sidequest.genre.models import (
    BeatDef,
    ConfrontationDef,
    MetricDef,
    ResolutionMode,
    ResourceDeclaration,
    RulesConfig,
)


class TestResourceDeclaration:
    def test_valid(self) -> None:
        r = ResourceDeclaration(
            name="luck", label="Luck", min=0.0, max=10.0, starting=5.0,
            voluntary=True, decay_per_turn=0.0,
        )
        assert r.name == "luck"

    def test_rejects_max_less_than_min(self) -> None:
        with pytest.raises(Exception, match="max.*must be.*min"):
            ResourceDeclaration(
                name="bad", label="Bad", min=10.0, max=5.0, starting=7.0,
                voluntary=True, decay_per_turn=0.0,
            )

    def test_rejects_starting_out_of_range(self) -> None:
        with pytest.raises(Exception, match="starting"):
            ResourceDeclaration(
                name="bad", label="Bad", min=0.0, max=10.0, starting=15.0,
                voluntary=True, decay_per_turn=0.0,
            )

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            ResourceDeclaration.model_validate({
                "name": "x", "label": "X", "min": 0, "max": 10, "starting": 5,
                "voluntary": True, "decay_per_turn": 0.0, "bogus": True,
            })


class TestMetricDef:
    def test_valid_directions(self) -> None:
        for direction in ("ascending", "descending", "bidirectional"):
            m = MetricDef(name="hp", direction=direction, starting=0)
            assert m.direction == direction

    def test_rejects_invalid_direction(self) -> None:
        with pytest.raises(Exception, match="invalid metric direction"):
            MetricDef(name="hp", direction="sideways", starting=0)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            MetricDef.model_validate({"name": "hp", "direction": "ascending", "starting": 0, "extra": True})


class TestBeatDef:
    def test_valid(self) -> None:
        b = BeatDef(id="attack", label="Attack", metric_delta=2, stat_check="STR")
        assert b.id == "attack"

    def test_rejects_empty_id(self) -> None:
        with pytest.raises(Exception):
            BeatDef(id="", label="Attack", metric_delta=2, stat_check="STR")

    def test_extra_forbidden(self) -> None:
        with pytest.raises(Exception):
            BeatDef.model_validate({
                "id": "attack", "label": "Attack", "metric_delta": 2,
                "stat_check": "STR", "unknown_field": True,
            })


class TestConfrontationDef:
    def _make(self, **kwargs: object) -> dict:
        base = {
            "type": "combat",
            "label": "Combat",
            "category": "combat",
            "metric": {"name": "hp", "direction": "ascending", "starting": 10},
            "beats": [{"id": "attack", "label": "Attack", "metric_delta": 2, "stat_check": "STR"}],
        }
        base.update(kwargs)
        return base

    def test_valid(self) -> None:
        c = ConfrontationDef.model_validate(self._make())
        assert c.confrontation_type == "combat"

    def test_rejects_invalid_category(self) -> None:
        with pytest.raises(Exception, match="invalid confrontation category"):
            ConfrontationDef.model_validate(self._make(category="invalid"))

    def test_rejects_empty_beats(self) -> None:
        with pytest.raises(Exception, match="at least one beat"):
            ConfrontationDef.model_validate(self._make(beats=[]))

    def test_rejects_duplicate_beat_ids(self) -> None:
        beats = [
            {"id": "attack", "label": "Attack", "metric_delta": 2, "stat_check": "STR"},
            {"id": "attack", "label": "Attack2", "metric_delta": 1, "stat_check": "STR"},
        ]
        with pytest.raises(Exception, match="duplicate beat id"):
            ConfrontationDef.model_validate(self._make(beats=beats))

    def test_resolution_mode_default(self) -> None:
        c = ConfrontationDef.model_validate(self._make())
        assert c.resolution_mode == ResolutionMode.beat_selection


class TestRulesConfig:
    def test_empty_defaults(self) -> None:
        r = RulesConfig()
        assert r.tone == ""
        assert r.confrontations == []
        assert r.resources == []

    def test_roundtrip(self) -> None:
        r = RulesConfig(tone="gritty", magic_level="none", ability_score_names=["STR", "DEX"])
        data = r.model_dump()
        r2 = RulesConfig.model_validate(data)
        assert r2.tone == "gritty"
        assert r2.ability_score_names == ["STR", "DEX"]
