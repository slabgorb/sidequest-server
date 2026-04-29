"""Tests for OceanProfile and DramaThresholds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models import DramaThresholds, OceanProfile


class TestOceanProfile:
    def test_defaults(self) -> None:
        p = OceanProfile()
        assert p.openness == 5.0
        assert p.neuroticism == 5.0

    def test_clamp_above_10(self) -> None:
        p = OceanProfile(openness=15.0)
        assert p.openness == 10.0

    def test_clamp_below_0(self) -> None:
        p = OceanProfile(conscientiousness=-3.0)
        assert p.conscientiousness == 0.0

    def test_valid_values(self) -> None:
        p = OceanProfile(openness=7.5, extraversion=2.3)
        assert p.openness == pytest.approx(7.5)
        assert p.extraversion == pytest.approx(2.3)

    def test_roundtrip(self) -> None:
        p = OceanProfile(openness=8.0, neuroticism=3.0)
        data = p.model_dump()
        p2 = OceanProfile.model_validate(data)
        assert p2.openness == pytest.approx(8.0)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            OceanProfile.model_validate({"openness": 5.0, "unknown_field": True})


class TestDramaThresholds:
    def test_defaults(self) -> None:
        dt = DramaThresholds()
        assert dt.sentence_delivery_min == pytest.approx(0.30)
        assert dt.render_threshold == pytest.approx(0.40)

    def test_roundtrip(self) -> None:
        dt = DramaThresholds(render_threshold=0.6)
        data = dt.model_dump()
        dt2 = DramaThresholds.model_validate(data)
        assert dt2.render_threshold == pytest.approx(0.6)

    def test_extra_ignored(self) -> None:
        """DramaThresholds uses extra=ignore to handle world pacing YAML nesting."""
        dt = DramaThresholds.model_validate({"render_threshold": 0.5, "extra_key": "ignored"})
        assert dt.render_threshold == pytest.approx(0.5)
