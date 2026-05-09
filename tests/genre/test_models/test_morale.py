"""Tests for MoraleDef and morale enums (B/X port)."""

import pytest
from pydantic import ValidationError

from sidequest.genre.models.rules import (
    BeatDef,
    BeatKind,
    ConfrontationDef,
    FleeConsequence,
    MetricDef,
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
    with pytest.raises(ValidationError, match="morale score"):
        MoraleDef(score=1, triggers=[MoraleTrigger.first_blood])


def test_morale_def_rejects_score_above_12():
    with pytest.raises(ValidationError, match="morale score"):
        MoraleDef(score=13, triggers=[MoraleTrigger.first_blood])


def test_morale_def_rejects_empty_triggers():
    with pytest.raises(ValidationError, match="morale.triggers"):
        MoraleDef(score=8, triggers=[])


def _minimal_combat_kwargs():
    return dict(
        type="combat",
        label="Test Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", starting=0, threshold=7),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=7),
        beats=[BeatDef(id="attack", label="Attack", kind=BeatKind.strike, stat_check="STR")],
    )


def test_confrontation_morale_defaults_none():
    cd = ConfrontationDef(**_minimal_combat_kwargs())
    assert cd.morale is None


def test_confrontation_accepts_morale_block():
    cd = ConfrontationDef(
        **_minimal_combat_kwargs(),
        morale=MoraleDef(score=8, triggers=[MoraleTrigger.first_blood]),
    )
    assert cd.morale is not None
    assert cd.morale.score == 8
