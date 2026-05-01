"""Clock primitive tests — story-time storage in hours."""
from __future__ import annotations

import pytest

from sidequest.orbital.clock import Clock


def test_clock_starts_at_zero():
    clock = Clock()
    assert clock.t_hours == 0.0


def test_clock_starts_at_explicit_epoch():
    clock = Clock(t_hours=72.0)
    assert clock.t_hours == 72.0


def test_clock_advance_adds_hours():
    clock = Clock()
    clock.advance(24.0)
    assert clock.t_hours == 24.0


def test_clock_advance_accumulates():
    clock = Clock()
    clock.advance(6.0)
    clock.advance(18.0)
    assert clock.t_hours == 24.0


def test_clock_advance_negative_rejected():
    clock = Clock()
    with pytest.raises(ValueError, match="negative"):
        clock.advance(-1.0)


def test_clock_advance_zero_allowed():
    clock = Clock()
    clock.advance(0.0)
    assert clock.t_hours == 0.0


def test_clock_t_days_is_t_hours_div_24():
    clock = Clock(t_hours=48.0)
    assert clock.t_days == 2.0
