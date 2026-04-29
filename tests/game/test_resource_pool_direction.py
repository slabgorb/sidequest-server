"""ResourceThreshold direction extension (Task 2.1).

Tests the `direction` field added to ResourceThreshold and the corresponding
detect_crossings update that honours it.

Note: detect_crossings signature is positional: (thresholds, old_value, new_value).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.game.resource_pool import ResourceThreshold
from sidequest.game.thresholds import detect_crossings


def test_default_direction_is_down():
    t = ResourceThreshold(at=0.40, event_id="bleed_through", narrator_hint="Bleeding")
    assert t.direction == "down"


def test_upward_threshold_fires_on_upward_crossing():
    t = ResourceThreshold(
        at=0.75, event_id="quiet_word", narrator_hint="Hegemony notice", direction="up"
    )
    crossings = detect_crossings([t], 0.70, 0.80)
    assert len(crossings) == 1
    assert crossings[0].event_id == "quiet_word"


def test_upward_threshold_does_not_fire_on_downward_crossing():
    t = ResourceThreshold(
        at=0.75, event_id="quiet_word", narrator_hint="x", direction="up"
    )
    crossings = detect_crossings([t], 0.80, 0.70)
    assert crossings == []


def test_invalid_direction_rejected():
    with pytest.raises(ValidationError):
        ResourceThreshold(at=0.5, event_id="x", narrator_hint="x", direction="sideways")
