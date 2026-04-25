"""Tests for the tier-aware dice resolver."""
from sidequest.game.dice import resolve_dice_with_faces
from sidequest.protocol.dice import DieSides, DieSpec, RollOutcome


def _d20(face: int):
    return [DieSpec(sides=DieSides.D20, count=1)], [face]


def test_tie_when_total_equals_difficulty_no_crit():
    dice, faces = _d20(10)
    # modifier 0, difficulty 10 → total 10 == DC → Tie
    resolved = resolve_dice_with_faces(dice, faces, modifier=0, difficulty=10)
    assert resolved.outcome is RollOutcome.Tie
    assert resolved.total == 10


def test_crit_success_by_margin_no_nat20():
    dice, faces = _d20(15)
    # modifier 0, difficulty 12 → margin 3 → CritSuccess
    resolved = resolve_dice_with_faces(dice, faces, modifier=0, difficulty=12)
    assert resolved.outcome is RollOutcome.CritSuccess


def test_crit_success_by_margin_just_under_threshold_is_success():
    dice, faces = _d20(14)
    # margin 2 → still Success, not CritSuccess
    resolved = resolve_dice_with_faces(dice, faces, modifier=0, difficulty=12)
    assert resolved.outcome is RollOutcome.Success


def test_nat20_still_crits_regardless_of_margin():
    dice, faces = _d20(20)
    resolved = resolve_dice_with_faces(dice, faces, modifier=0, difficulty=30)
    # total 20 < DC 30 but nat-20 wins
    assert resolved.outcome is RollOutcome.CritSuccess


def test_nat1_still_critfails_regardless_of_total():
    dice = [DieSpec(sides=DieSides.D20, count=1)]
    resolved = resolve_dice_with_faces(dice, [1], modifier=100, difficulty=5)
    # total 101 >> DC 5 but nat-1 wins
    assert resolved.outcome is RollOutcome.CritFail


def test_fail_when_total_below_difficulty():
    dice, faces = _d20(5)
    resolved = resolve_dice_with_faces(dice, faces, modifier=0, difficulty=15)
    assert resolved.outcome is RollOutcome.Fail
