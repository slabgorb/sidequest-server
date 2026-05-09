"""Tests for the ADR-094 label_strategy module.

Pinned to ADR-094 acceptance criteria (AC-S*, AC-G*, AC-L*, AC-C*,
AC-A*, AC-O*) per docs/superpowers/specs/2026-05-04-adr-094-...
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from sidequest.orbital import palette
from sidequest.orbital.label_strategy import (
    CalloutBlock,
    LabelDecision,
    LabelStrategy,
    SelectionReason,
    _apply_decision_tree,
    _block_height_px,
    _count_cross_group_crossings,
    _group_callouts_by_parent,
    _rule_explicit_callout_label,
    _rule_forced_moon_band,
    _rule_radial,
    _rule_textpath,
    _segments_intersect,
    _side_for_bearing,
    _StrategyInput,
    estimate_text_width_px,
    lay_out_gutter,
    select_label_strategies,
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


def _make_decision(body_id, parent_id, semi_major_au=1.0, tag=None) -> LabelDecision:
    return LabelDecision(
        body_id=body_id,
        parent_id=parent_id,
        parent_type="companion" if parent_id else None,
        strategy=LabelStrategy.CALLOUT,
        reason=SelectionReason.FORCED_MOON_BAND,
        text=body_id.upper(),
        register="engraved",
        text_width_px=50.0,
        radial_tier=None,
        arc_available_px=None,
        textpath_path_id=None,
        path_circumference_px=None,
        callout_tag=tag,
    )


class TestGroupCallouts:
    def test_three_or_more_siblings_form_group(self):
        decisions = [
            _make_decision("c1", "parent_a"),
            _make_decision("c2", "parent_a"),
            _make_decision("c3", "parent_a"),
        ]
        groups = _group_callouts_by_parent(
            decisions,
            semi_major_by_id={"c1": 0.005, "c2": 0.010, "c3": 0.015},
        )
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_two_siblings_remain_singletons(self):
        decisions = [
            _make_decision("c1", "parent_a"),
            _make_decision("c2", "parent_a"),
        ]
        groups = _group_callouts_by_parent(
            decisions,
            semi_major_by_id={"c1": 0.005, "c2": 0.010},
        )
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    def test_orphan_callouts_are_singletons(self):
        decisions = [
            LabelDecision(
                body_id="x",
                parent_id=None,
                parent_type=None,
                strategy=LabelStrategy.CALLOUT,
                reason=SelectionReason.FALLBACK_ARC_TOO_SHORT,
                text="X",
                register="engraved",
                text_width_px=50.0,
                radial_tier=None,
                arc_available_px=None,
                textpath_path_id=None,
                path_circumference_px=None,
                callout_tag=None,
            ),
        ]
        groups = _group_callouts_by_parent(decisions, semi_major_by_id={})
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_group_members_sorted_by_semi_major_au_ascending(self):
        decisions = [
            _make_decision("outer", "parent_a"),
            _make_decision("inner", "parent_a"),
            _make_decision("middle", "parent_a"),
        ]
        groups = _group_callouts_by_parent(
            decisions,
            semi_major_by_id={"inner": 0.005, "middle": 0.010, "outer": 0.020},
        )
        assert len(groups) == 1
        ids = [d.body_id for d in groups[0]]
        assert ids == ["inner", "middle", "outer"]


class TestBlockHeight:
    def test_singleton_no_tag(self):
        d = _make_decision("body_x", None)
        h = _block_height_px((d,), is_grouped=False)
        expected = 2 * palette.CALLOUT_BLOCK_PADDING_PX + palette.CALLOUT_BLOCK_LINE_HEIGHT_PX
        assert h == pytest.approx(expected)

    def test_singleton_with_tag(self):
        d = _make_decision("body_x", None, tag="habitat · 1.0 AU")
        h = _block_height_px((d,), is_grouped=False)
        expected = (
            2 * palette.CALLOUT_BLOCK_PADDING_PX
            + palette.CALLOUT_BLOCK_LINE_HEIGHT_PX
            + palette.CALLOUT_BLOCK_TAG_LINE_HEIGHT_PX
        )
        assert h == pytest.approx(expected)

    def test_grouped_block_height_scales_with_member_count(self):
        members = tuple(_make_decision(f"c{i}", "parent_a") for i in range(6))
        h = _block_height_px(members, is_grouped=True)
        expected = (
            2 * palette.CALLOUT_BLOCK_PADDING_PX
            + palette.CALLOUT_GROUP_TITLE_HEIGHT_PX
            + 6 * palette.CALLOUT_BLOCK_LINE_HEIGHT_PX
        )
        assert h == pytest.approx(expected)


class TestSideForBearing:
    def test_right_half(self):
        assert _side_for_bearing(0.0) == "right"
        assert _side_for_bearing(45.0) == "right"
        assert _side_for_bearing(89.999) == "right"
        assert _side_for_bearing(270.0) == "right"
        assert _side_for_bearing(330.0) == "right"

    def test_left_half(self):
        assert _side_for_bearing(91.0) == "left"
        assert _side_for_bearing(180.0) == "left"
        assert _side_for_bearing(269.999) == "left"

    def test_boundary_90_deg_goes_left(self):
        assert _side_for_bearing(90.0) == "left"
        assert _side_for_bearing(270.0) == "right"


@dataclass(frozen=True)
class _FakeViewport:
    chart_min_x: float
    chart_max_x: float
    chart_top_y: float
    chart_bottom_y: float
    svg_min_x: float
    svg_max_x: float


def _viewport_default() -> _FakeViewport:
    return _FakeViewport(
        chart_min_x=-100,
        chart_max_x=100,
        chart_top_y=-100,
        chart_bottom_y=100,
        svg_min_x=-220,
        svg_max_x=220,
    )


class TestLayOutGutter:
    def test_empty_decisions_empty_layout(self):
        layout = lay_out_gutter(
            decisions=[],
            anchor_by_id={},
            semi_major_by_id={},
            viewport=_viewport_default(),
        )
        assert layout.blocks == ()
        assert layout.inset_fallback_count == 0
        assert layout.cross_group_crossing_count == 0

    def test_single_callout_lands_on_correct_side(self):
        d = _make_decision("body_x", None)
        layout = lay_out_gutter(
            decisions=[d],
            anchor_by_id={"body_x": (50.0, -10.0, 11.3)},
            semi_major_by_id={},
            viewport=_viewport_default(),
        )
        assert len(layout.blocks) == 1
        assert layout.blocks[0].side == "right"
        assert layout.blocks[0].block_x > 100

    def test_within_side_sorted_by_bearing_top_down(self):
        d_top = _make_decision("top", None)
        d_bot = _make_decision("bot", None)
        layout = lay_out_gutter(
            decisions=[d_bot, d_top],
            anchor_by_id={"top": (10.0, -50.0, 80.0), "bot": (50.0, -10.0, 11.3)},
            semi_major_by_id={},
            viewport=_viewport_default(),
        )
        assert len(layout.blocks) == 2
        ys = [b.block_y for b in layout.blocks]
        assert ys[0] < ys[1]

    def test_grouped_block_for_companion_children(self):
        decisions = [_make_decision(f"x{i}", "parent_a") for i in range(3)]
        layout = lay_out_gutter(
            decisions=decisions,
            anchor_by_id={f"x{i}": (50.0, -10.0 + i, 30.0) for i in range(3)},
            semi_major_by_id={"x0": 0.005, "x1": 0.010, "x2": 0.015},
            viewport=_viewport_default(),
        )
        assert len(layout.blocks) == 1
        b = layout.blocks[0]
        assert len(b.members) == 3

    def test_overflow_into_inset(self):
        decisions = [_make_decision(f"b{i}", None) for i in range(20)]
        anchors = {f"b{i}": (50.0, -10.0 + i * 0.001, 30.0) for i in range(20)}
        layout = lay_out_gutter(
            decisions=decisions,
            anchor_by_id=anchors,
            semi_major_by_id={},
            viewport=_viewport_default(),
        )
        assert layout.inset_fallback_count > 0
        sides = [b.side for b in layout.blocks]
        assert "inset" in sides


def _solo_block(anchor_x, anchor_y, bearing, bx, by, w, h):
    d = _make_decision("x", None)
    return CalloutBlock(
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        anchor_bearing_deg=bearing,
        side="right",
        parent_label=None,
        members=(d,),
        block_x=bx,
        block_y=by,
        block_width_px=w,
        block_height_px=h,
    )


class TestCrossGroupCrossings:
    def test_segments_intersect_basic(self):
        assert _segments_intersect((0, 0), (10, 10), (0, 10), (10, 0))

    def test_segments_parallel_no_cross(self):
        assert not _segments_intersect((0, 0), (10, 0), (0, 1), (10, 1))

    def test_segments_share_endpoint_no_cross(self):
        assert not _segments_intersect((0, 0), (10, 0), (10, 0), (10, 10))

    def test_no_crossings_when_one_block(self):
        b = _solo_block(10, 0, 0, 100, 50, 20, 30)
        assert _count_cross_group_crossings([b]) == 0

    def test_one_crossing(self):
        b1 = _solo_block(anchor_x=10, anchor_y=10, bearing=10, bx=100, by=20, w=50, h=20)
        b2 = _solo_block(anchor_x=10, anchor_y=80, bearing=20, bx=100, by=0, w=50, h=20)
        assert _count_cross_group_crossings([b1, b2]) == 1


class TestSelectLabelStrategies:
    def test_empty_inputs_empty_decisions(self):
        decisions = select_label_strategies(inputs=[])
        assert decisions == []

    def test_one_input_one_decision(self):
        inp = _make_input(arc_to_neighbor_px=500.0, text_width_px=50.0, radial_tier=0)
        decisions = select_label_strategies(inputs=[inp])
        assert len(decisions) == 1
        assert decisions[0].strategy == LabelStrategy.RADIAL

    def test_each_body_gets_independent_decision(self):
        inp_radial = _make_input(body_id="a", arc_to_neighbor_px=500.0, text_width_px=50.0)
        inp_callout = _make_input(body_id="b", is_moon_band_child=True)
        decisions = select_label_strategies(inputs=[inp_radial, inp_callout])
        assert {d.body_id for d in decisions} == {"a", "b"}
        by_id = {d.body_id: d for d in decisions}
        assert by_id["a"].strategy == LabelStrategy.RADIAL
        assert by_id["b"].strategy == LabelStrategy.CALLOUT
