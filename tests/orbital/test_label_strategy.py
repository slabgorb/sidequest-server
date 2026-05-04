"""Tests for the ADR-094 label_strategy module.

Pinned to ADR-094 acceptance criteria (AC-S*, AC-G*, AC-L*, AC-C*,
AC-A*, AC-O*) per docs/superpowers/specs/2026-05-04-adr-094-...
"""

from __future__ import annotations

import pytest

from sidequest.orbital import palette
from sidequest.orbital.label_strategy import (
    estimate_text_width_px,
)


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
