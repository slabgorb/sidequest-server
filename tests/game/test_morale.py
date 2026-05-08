"""Tests for maybe_check_morale — B/X 2d6 morale check, side-level outcome."""

import random

from sidequest.game.morale import (
    MoraleOutcome,
    OpponentSideState,
    OpponentState,
    maybe_check_morale,
)
from sidequest.genre.models.rules import (
    BeatDef,
    BeatKind,
    ConfrontationDef,
    FleeConsequence,
    MetricDef,
    MoraleDef,
    MoraleTrigger,
)


def _confrontation(*, morale=None):
    return ConfrontationDef(
        type="combat",
        label="C",
        category="combat",
        player_metric=MetricDef(name="m", starting=0, threshold=7),
        opponent_metric=MetricDef(name="m", starting=0, threshold=7),
        beats=[BeatDef(id="attack", label="A", kind=BeatKind.strike, stat_check="STR")],
        morale=morale,
    )


def _side(opponents):
    return OpponentSideState(label="goblins", opponents=opponents)


def _opp(id_, *, mindless=False, alive=True, is_leader=False):
    return OpponentState(id=id_, mindless=mindless, alive=alive, is_leader=is_leader)


def _morale(score=8, triggers=None, flee=FleeConsequence.chase):
    return MoraleDef(
        score=score,
        triggers=triggers or [MoraleTrigger.first_blood, MoraleTrigger.half_killed],
        flee_consequence=flee,
    )


def test_no_morale_block_returns_stay():
    cd = _confrontation(morale=None)
    out = maybe_check_morale(cd, _side([_opp("g1")]), MoraleTrigger.first_blood, random.Random(0))
    assert out is MoraleOutcome.stay


def test_trigger_not_in_morale_returns_stay():
    cd = _confrontation(morale=_morale(triggers=[MoraleTrigger.first_blood]))
    out = maybe_check_morale(cd, _side([_opp("g1")]), MoraleTrigger.intimidated, random.Random(0))
    assert out is MoraleOutcome.stay


def test_total_le_score_returns_stay():
    cd = _confrontation(morale=_morale(score=8))
    rng = random.Random(42)
    out = maybe_check_morale(cd, _side([_opp("g1")]), MoraleTrigger.first_blood, rng)
    assert out is MoraleOutcome.stay


def test_total_gt_score_returns_flee():
    cd = _confrontation(morale=_morale(score=2))  # almost always > 2
    rng = random.Random(42)
    out = maybe_check_morale(cd, _side([_opp("g1")]), MoraleTrigger.first_blood, rng)
    assert out is MoraleOutcome.flee


def test_all_mindless_side_returns_stay_regardless_of_roll():
    cd = _confrontation(morale=_morale(score=2))
    side = _side([_opp("s1", mindless=True), _opp("s2", mindless=True)])
    out = maybe_check_morale(cd, side, MoraleTrigger.first_blood, random.Random(0))
    assert out is MoraleOutcome.stay


def test_mixed_mindless_side_rolls_for_non_mindless_only():
    cd = _confrontation(morale=_morale(score=2))
    side = _side(
        [
            _opp("s1", mindless=True),
            _opp("g1", mindless=False),
        ]
    )
    out = maybe_check_morale(cd, side, MoraleTrigger.first_blood, random.Random(42))
    assert out is MoraleOutcome.flee
