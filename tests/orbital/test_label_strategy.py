"""Tests for the ADR-094 label_strategy module.

Pinned to ADR-094 acceptance criteria (AC-S*, AC-G*, AC-L*, AC-C*,
AC-A*, AC-O*) per docs/superpowers/specs/2026-05-04-adr-094-...
"""

from __future__ import annotations

import pytest

from sidequest.orbital import palette
from sidequest.orbital.label_strategy import (
    LabelStrategy,
    SelectionReason,
    _StrategyInput,
    _apply_decision_tree,
    _rule_explicit_callout_label,
    _rule_forced_moon_band,
    _rule_radial,
    _rule_textpath,
    estimate_text_width_px,
)
from sidequest.orbital.models import Annotation


def _make_input(**overrides) -> _StrategyInput:
    """Helper: minimal _StrategyInput with sensible defaults."""
    defaults = dict(
        body_id="body_x",
        parent_id="parent_a",
        parent_type="habitat",
        text="BODY X",
        register="engraved",
        text_width_px=50.0,
        is_moon_band_child=False,
        callout_label_annotation=None,
        textpath_path_id=None,
        path_circumference_px=None,
        arc_to_neighbor_px=200.0,
        radial_tier=0,
        anchor_x=100.0,
        anchor_y=50.0,
        anchor_bearing_deg=45.0,
        callout_tag=None,
    )
    defaults.update(overrides)
    return _StrategyInput(**defaults)


class TestEstimateTextWidth:
    def test_engraved_uses_engraved_constant(self):
        w = estimate_text_width_px("ABC", "engraved")
        assert w == pytest.approx(3 * palette.LABEL_ENGRAVED_CHAR_WIDTH_PX)

    def test_chalk_uses_chalk_constant(self):
        w = estimate_text_width_px("ABCDE", "chalk")
        assert w == pytest.approx(5 * palette.LABEL_CHALK_CHAR_WIDTH_PX)

    def test_prose_uses_prose_constant(self):
        w = estimate_text_width_px("hello", "prose")
        assert w == pytest.approx(5 * palette.LABEL_PROSE_CHAR_WIDTH_PX)

    def test_empty_string_zero_width(self):
        assert estimate_text_width_px("", "engraved") == 0.0

    def test_unknown_register_raises(self):
        with pytest.raises(ValueError, match="unknown register"):
            estimate_text_width_px("ABC", "carved")  # type: ignore[arg-type]


class TestRuleForcedMoonBand:
    def test_moon_band_child_with_label_returns_callout(self):
        inp = _make_input(is_moon_band_child=True, parent_type="habitat")
        decision = _rule_forced_moon_band(inp)
        assert decision is not None
        assert decision.strategy == LabelStrategy.CALLOUT
        assert decision.reason == SelectionReason.FORCED_MOON_BAND

    def test_moon_band_child_companion_parent_returns_callout(self):
        inp = _make_input(is_moon_band_child=True, parent_type="companion")
        decision = _rule_forced_moon_band(inp)
        assert decision is not None
        assert decision.strategy == LabelStrategy.CALLOUT
        assert decision.reason == SelectionReason.FORCED_MOON_BAND

    def test_top_level_body_returns_none(self):
        inp = _make_input(is_moon_band_child=False)
        assert _rule_forced_moon_band(inp) is None

    def test_decision_carries_text_and_register(self):
        inp = _make_input(is_moon_band_child=True, text="VAEL THAIN", register="engraved")
        d = _rule_forced_moon_band(inp)
        assert d is not None
        assert d.text == "VAEL THAIN"
        assert d.register == "engraved"
        assert d.body_id == "body_x"
        assert d.parent_id == "parent_a"
        assert d.parent_type == "habitat"


class TestRuleExplicitCalloutLabel:
    def test_callout_label_annotation_returns_callout(self):
        annot = Annotation(
            kind="callout_label",
            text="SPREAD ALPHA",
            body_ref="body_x",
            tag="habitat · 3.0 AU",
        )
        inp = _make_input(callout_label_annotation=annot, callout_tag="habitat · 3.0 AU")
        d = _rule_explicit_callout_label(inp)
        assert d is not None
        assert d.strategy == LabelStrategy.CALLOUT
        assert d.reason == SelectionReason.EXPLICIT_CALLOUT_LABEL
        assert d.callout_tag == "habitat · 3.0 AU"

    def test_no_annotation_returns_none(self):
        inp = _make_input(callout_label_annotation=None)
        assert _rule_explicit_callout_label(inp) is None


class TestRuleTextpath:
    def test_textpath_fits_returns_decision(self):
        inp = _make_input(
            textpath_path_id="orbit_outer",
            path_circumference_px=200.0,
            text_width_px=50.0,
        )
        decision, latent = _rule_textpath(inp)
        assert latent is None
        assert decision is not None
        assert decision.strategy == LabelStrategy.TEXTPATH
        assert decision.reason == SelectionReason.TEXTPATH_FITS
        assert decision.textpath_path_id == "orbit_outer"
        assert decision.path_circumference_px == 200.0

    def test_textpath_too_short_returns_latent_reason(self):
        inp = _make_input(
            textpath_path_id="body:tiny_belt",
            path_circumference_px=50.0,
            text_width_px=50.0,
        )
        decision, latent = _rule_textpath(inp)
        assert decision is None
        assert latent == SelectionReason.FALLBACK_TEXTPATH_TOO_SHORT

    def test_no_textpath_annotation_returns_none_none(self):
        inp = _make_input(textpath_path_id=None, path_circumference_px=None)
        decision, latent = _rule_textpath(inp)
        assert decision is None
        assert latent is None

    def test_safety_factor_boundary_inclusive(self):
        inp = _make_input(
            textpath_path_id="orbit_outer",
            path_circumference_px=60.0,
            text_width_px=50.0,
        )
        decision, latent = _rule_textpath(inp)
        assert decision is not None
        assert decision.strategy == LabelStrategy.TEXTPATH


class TestRuleRadial:
    def test_radial_fits_returns_decision(self):
        inp = _make_input(
            arc_to_neighbor_px=200.0,
            text_width_px=50.0,
            radial_tier=0,
        )
        decision, latent = _rule_radial(inp)
        assert latent is None
        assert decision is not None
        assert decision.strategy == LabelStrategy.RADIAL
        assert decision.reason == SelectionReason.RADIAL_FITS
        assert decision.radial_tier == 0
        assert decision.arc_available_px == 200.0

    def test_arc_too_short_returns_latent(self):
        inp = _make_input(
            arc_to_neighbor_px=30.0,
            text_width_px=50.0,
            radial_tier=0,
        )
        decision, latent = _rule_radial(inp)
        assert decision is None
        assert latent == SelectionReason.FALLBACK_ARC_TOO_SHORT

    def test_tier_capped_returns_latent(self):
        inp = _make_input(
            arc_to_neighbor_px=500.0,
            text_width_px=50.0,
            radial_tier=palette.LABEL_TIER_MAX + 1,
        )
        decision, latent = _rule_radial(inp)
        assert decision is None
        assert latent == SelectionReason.FALLBACK_TIER_CAPPED

    def test_arc_too_short_takes_priority_over_tier_capped(self):
        inp = _make_input(
            arc_to_neighbor_px=10.0,
            text_width_px=50.0,
            radial_tier=palette.LABEL_TIER_MAX + 5,
        )
        _, latent = _rule_radial(inp)
        assert latent == SelectionReason.FALLBACK_ARC_TOO_SHORT

    def test_no_arc_data_returns_none_none(self):
        inp = _make_input(arc_to_neighbor_px=None, text_width_px=50.0)
        decision, latent = _rule_radial(inp)
        assert decision is None
        assert latent is None

    def test_safety_factor_boundary(self):
        inp = _make_input(
            arc_to_neighbor_px=60.0,
            text_width_px=50.0,
            radial_tier=0,
        )
        decision, _ = _rule_radial(inp)
        assert decision is not None


class TestDecisionTreePrecedence:
    def test_forced_moon_band_beats_explicit_callout_label(self):
        annot = Annotation(kind="callout_label", text="X", body_ref="body_x")
        inp = _make_input(
            is_moon_band_child=True,
            parent_type="companion",
            callout_label_annotation=annot,
        )
        d = _apply_decision_tree(inp)
        assert d.reason == SelectionReason.FORCED_MOON_BAND

    def test_forced_moon_band_beats_textpath(self):
        inp = _make_input(
            is_moon_band_child=True,
            textpath_path_id="orbit_x",
            path_circumference_px=500.0,
        )
        d = _apply_decision_tree(inp)
        assert d.reason == SelectionReason.FORCED_MOON_BAND

    def test_explicit_callout_label_beats_textpath(self):
        annot = Annotation(kind="callout_label", text="X", body_ref="body_x")
        inp = _make_input(
            callout_label_annotation=annot,
            textpath_path_id="orbit_x",
            path_circumference_px=500.0,
        )
        d = _apply_decision_tree(inp)
        assert d.reason == SelectionReason.EXPLICIT_CALLOUT_LABEL

    def test_textpath_beats_radial(self):
        inp = _make_input(
            textpath_path_id="orbit_x",
            path_circumference_px=500.0,
            arc_to_neighbor_px=500.0,
        )
        d = _apply_decision_tree(inp)
        assert d.reason == SelectionReason.TEXTPATH_FITS

    def test_textpath_too_short_falls_to_callout_not_radial(self):
        inp = _make_input(
            textpath_path_id="body:tiny",
            path_circumference_px=10.0,
            text_width_px=50.0,
            arc_to_neighbor_px=500.0,
            radial_tier=0,
        )
        d = _apply_decision_tree(inp)
        assert d.strategy == LabelStrategy.CALLOUT
        assert d.reason == SelectionReason.FALLBACK_TEXTPATH_TOO_SHORT

    def test_radial_when_no_other_rule_fires(self):
        inp = _make_input(arc_to_neighbor_px=500.0, text_width_px=50.0, radial_tier=0)
        d = _apply_decision_tree(inp)
        assert d.strategy == LabelStrategy.RADIAL

    def test_arc_too_short_fallback_callout(self):
        inp = _make_input(arc_to_neighbor_px=10.0, text_width_px=50.0)
        d = _apply_decision_tree(inp)
        assert d.strategy == LabelStrategy.CALLOUT
        assert d.reason == SelectionReason.FALLBACK_ARC_TOO_SHORT

    def test_tier_capped_fallback_callout(self):
        inp = _make_input(
            arc_to_neighbor_px=500.0,
            text_width_px=50.0,
            radial_tier=palette.LABEL_TIER_MAX + 1,
        )
        d = _apply_decision_tree(inp)
        assert d.strategy == LabelStrategy.CALLOUT
        assert d.reason == SelectionReason.FALLBACK_TIER_CAPPED

    def test_no_data_fallback_callout(self):
        inp = _make_input(arc_to_neighbor_px=None)
        d = _apply_decision_tree(inp)
        assert d.strategy == LabelStrategy.CALLOUT
