"""Tests for MoraleDef and morale enums (B/X port)."""

import pytest
from pydantic import ValidationError

from sidequest.genre.models.rules import (
    FleeConsequence,
    MoraleDef,
    MoraleTrigger,
)


def test_morale_trigger_enum_values():
    assert {t.value for t in MoraleTrigger} == {
        "first_blood",
        "half_killed",
        "intimidated",
        "leader_killed",
    }


def test_flee_consequence_enum_values():
    assert {f.value for f in FleeConsequence} == {"chase", "surrender", "rout"}


def test_morale_def_defaults_score_8_chase():
    m = MoraleDef(triggers=[MoraleTrigger.first_blood])
    assert m.score == 8
    assert m.flee_consequence is FleeConsequence.chase


def test_morale_def_rejects_score_below_2():
    with pytest.raises(ValidationError):
        MoraleDef(score=1, triggers=[MoraleTrigger.first_blood])


def test_morale_def_rejects_score_above_12():
    with pytest.raises(ValidationError):
        MoraleDef(score=13, triggers=[MoraleTrigger.first_blood])


def test_morale_def_rejects_empty_triggers():
    with pytest.raises(ValidationError):
        MoraleDef(score=8, triggers=[])
