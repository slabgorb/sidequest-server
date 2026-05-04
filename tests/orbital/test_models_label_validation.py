"""Validation tests for BodyDef.label and Annotation.tag (ADR-094)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.orbital.models import BodyDef, BodyType


class TestBodyDefLabelValidation:
    def test_label_blank_string_rejected(self):
        with pytest.raises(ValidationError, match="label must be non-empty"):
            BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=1.0,
                period_days=365.0,
                epoch_phase_deg=0,
                label="   ",
            )

    def test_label_empty_string_rejected(self):
        with pytest.raises(ValidationError, match="label must be non-empty"):
            BodyDef(
                type=BodyType.HABITAT,
                parent="sun",
                semi_major_au=1.0,
                period_days=365.0,
                epoch_phase_deg=0,
                label="",
            )

    def test_label_none_accepted(self):
        body = BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.0,
            period_days=365.0,
            epoch_phase_deg=0,
            label=None,
        )
        assert body.label is None

    def test_label_normal_string_accepted(self):
        body = BodyDef(
            type=BodyType.HABITAT,
            parent="sun",
            semi_major_au=1.0,
            period_days=365.0,
            epoch_phase_deg=0,
            label="HABITAT ALPHA",
        )
        assert body.label == "HABITAT ALPHA"
