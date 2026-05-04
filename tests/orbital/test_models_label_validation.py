"""Validation tests for BodyDef.label and Annotation.tag (ADR-094)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.orbital.models import KNOWN_ANNOTATION_KINDS, Annotation, BodyDef, BodyType


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


class TestCalloutLabelAnnotation:
    def test_callout_label_in_known_kinds(self):
        assert "callout_label" in KNOWN_ANNOTATION_KINDS

    def test_callout_label_basic(self):
        a = Annotation(
            kind="callout_label",
            text="VAEL THAIN",
            body_ref="vael_thain",
        )
        assert a.kind == "callout_label"
        assert a.text == "VAEL THAIN"
        assert a.body_ref == "vael_thain"
        assert a.tag is None

    def test_callout_label_with_tag(self):
        a = Annotation(
            kind="callout_label",
            text="VAEL THAIN",
            body_ref="vael_thain",
            tag="habitat · 1.68M km",
        )
        assert a.tag == "habitat · 1.68M km"

    def test_callout_label_missing_text_rejected(self):
        with pytest.raises(ValidationError, match="callout_label requires non-empty text"):
            Annotation(kind="callout_label", body_ref="vael_thain")

    def test_callout_label_blank_text_rejected(self):
        with pytest.raises(ValidationError, match="callout_label requires non-empty text"):
            Annotation(kind="callout_label", text="   ", body_ref="vael_thain")

    def test_callout_label_missing_body_ref_rejected(self):
        with pytest.raises(ValidationError, match="callout_label requires body_ref"):
            Annotation(kind="callout_label", text="VAEL THAIN")

    def test_callout_label_tag_too_long_rejected(self):
        with pytest.raises(ValidationError, match="exceeds 24 chars"):
            Annotation(
                kind="callout_label",
                text="VAEL THAIN",
                body_ref="vael_thain",
                tag="x" * 25,
            )

    def test_callout_label_tag_at_limit_accepted(self):
        a = Annotation(
            kind="callout_label",
            text="VAEL THAIN",
            body_ref="vael_thain",
            tag="x" * 24,
        )
        assert a.tag is not None
        assert len(a.tag) == 24

    def test_unknown_kind_still_rejected(self):
        with pytest.raises(ValidationError, match="unknown annotation kind"):
            Annotation(kind="not_a_real_kind", text="x")
