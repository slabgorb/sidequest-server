"""Tests for OpeningSetting — exactly-one-anchor invariant (validators 9, 12)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models.narrative import OpeningSetting


def test_chassis_anchored_minimal() -> None:
    s = OpeningSetting(chassis_instance="kestrel", interior_room="galley")
    assert s.chassis_instance == "kestrel"
    assert s.interior_room == "galley"
    assert s.location_label is None
    assert s.present_npcs == []


def test_location_anchored_minimal() -> None:
    s = OpeningSetting(location_label="the Imperatrix's Arena, threshold gate")
    assert s.location_label is not None
    assert s.chassis_instance is None
    assert s.interior_room is None


def test_location_anchored_with_present_npcs() -> None:
    s = OpeningSetting(
        location_label="the Promenade",
        present_npcs=["arena_master", "patron_celestine"],
    )
    assert s.present_npcs == ["arena_master", "patron_celestine"]


def test_both_anchors_rejected() -> None:
    """Validator 9: exactly one anchor."""
    with pytest.raises(ValidationError, match="exactly one"):
        OpeningSetting(
            chassis_instance="kestrel",
            interior_room="galley",
            location_label="the Promenade",
        )


def test_neither_anchor_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        OpeningSetting()


def test_chassis_without_room_rejected() -> None:
    with pytest.raises(ValidationError, match="interior_room required"):
        OpeningSetting(chassis_instance="kestrel")


def test_chassis_with_present_npcs_rejected() -> None:
    """Validator 12 part-a: ship-anchored openings must not declare present_npcs;
    they come from chassis.crew_npcs."""
    with pytest.raises(ValidationError, match="present_npcs must be empty"):
        OpeningSetting(
            chassis_instance="kestrel",
            interior_room="galley",
            present_npcs=["someone"],
        )
