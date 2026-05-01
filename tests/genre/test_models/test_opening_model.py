"""Tests for the top-level Opening model.

Validators on this model: 1 (no `?` in first_turn_invitation),
10 (no [authored]/[TBD]/[migrated]/[placeholder] markers in prose fields).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models.narrative import (
    Opening,
    OpeningSetting,
    OpeningTrigger,
)


def _minimal_kwargs() -> dict:
    return {
        "id": "test_opening",
        "triggers": OpeningTrigger(mode="solo"),
        "setting": OpeningSetting(chassis_instance="kestrel", interior_room="galley"),
        "establishing_narration": "The galley is warm. The coffee is cold on the third sip.",
        "first_turn_invitation": "Outside the porthole: void, stars, the long indifferent gradient.",
    }


def test_minimal_opening_parses() -> None:
    op = Opening(**_minimal_kwargs())
    assert op.id == "test_opening"
    assert op.party_framing is None
    assert op.magic_microbleed is None
    assert op.rig_voice_seeds == []


def test_first_turn_invitation_with_question_rejected() -> None:
    """Validator 1: no `?` in first_turn_invitation."""
    kw = _minimal_kwargs()
    kw["first_turn_invitation"] = "What does each of you do?"
    with pytest.raises(ValidationError, match="must not contain '\\?'"):
        Opening(**kw)


def test_establishing_narration_with_authored_marker_rejected() -> None:
    """Validator 10: placeholder markers fail loud at parse."""
    kw = _minimal_kwargs()
    kw["establishing_narration"] = "[authored — galley scene goes here]"
    with pytest.raises(ValidationError, match="placeholder marker"):
        Opening(**kw)


def test_first_turn_invitation_with_tbd_marker_rejected() -> None:
    kw = _minimal_kwargs()
    kw["first_turn_invitation"] = "[TBD — closing line]"
    with pytest.raises(ValidationError, match="placeholder marker"):
        Opening(**kw)


def test_establishing_narration_with_migrated_marker_rejected() -> None:
    kw = _minimal_kwargs()
    kw["establishing_narration"] = "[migrated from mp_opening.yaml]"
    with pytest.raises(ValidationError, match="placeholder marker"):
        Opening(**kw)


def test_establishing_narration_with_placeholder_marker_rejected() -> None:
    kw = _minimal_kwargs()
    kw["establishing_narration"] = "[placeholder text]"
    with pytest.raises(ValidationError, match="placeholder marker"):
        Opening(**kw)


def test_extra_top_level_fields_allowed() -> None:
    """Top-level Opening uses extra='allow' so authors can experiment."""
    kw = _minimal_kwargs()
    kw["world_specific_field"] = "experimental content"
    op = Opening(**kw)
    assert op.id == "test_opening"


def test_question_in_establishing_narration_allowed() -> None:
    """`?` is only forbidden in first_turn_invitation, not the wider scene."""
    kw = _minimal_kwargs()
    kw["establishing_narration"] = "Is the coffee actually coffee? It is not."
    op = Opening(**kw)
    assert "?" in op.establishing_narration
