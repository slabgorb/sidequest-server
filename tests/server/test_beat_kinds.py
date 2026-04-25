import pytest

from sidequest.game.beat_kinds import (
    DEFAULT_DELTAS,
    BeatKind,
    ResolvedDeltas,
    resolve_tier_deltas,
)
from sidequest.protocol.dice import RollOutcome

# ---- BeatKind enum -------------------------------------------------------

def test_beat_kind_members():
    assert {k.value for k in BeatKind} == {"strike", "brace", "push", "angle"}


# ---- strike defaults -----------------------------------------------------

def test_strike_success_advances_own_by_base():
    deltas = resolve_tier_deltas(
        kind=BeatKind.strike, base=3, outcome=RollOutcome.Success,
        overrides=None, target_tag=None,
    )
    assert deltas == ResolvedDeltas(own=3, opponent=0)


def test_strike_tie_is_graze_half_base_floor():
    deltas = resolve_tier_deltas(
        kind=BeatKind.strike, base=3, outcome=RollOutcome.Tie,
        overrides=None, target_tag=None,
    )
    assert deltas.own == 1  # 3 // 2
    assert deltas.opponent == 0


def test_strike_critsuccess_grants_fleeting_opening_tag():
    deltas = resolve_tier_deltas(
        kind=BeatKind.strike, base=3, outcome=RollOutcome.CritSuccess,
        overrides=None, target_tag=None,
    )
    assert deltas.own == 3
    assert deltas.opponent == 0
    assert deltas.grants_fleeting_tag == "Opening"


def test_strike_fail_and_critfail_zero():
    for tier in (RollOutcome.Fail, RollOutcome.CritFail):
        deltas = resolve_tier_deltas(
            kind=BeatKind.strike, base=3, outcome=tier,
            overrides=None, target_tag=None,
        )
        assert deltas.own == 0
        assert deltas.opponent == 0


# ---- brace defaults ------------------------------------------------------

def test_brace_success_drains_opponent_by_base():
    deltas = resolve_tier_deltas(
        kind=BeatKind.brace, base=2, outcome=RollOutcome.Success,
        overrides=None, target_tag=None,
    )
    # Brace pushes opponent dial *backward* — implemented as negative delta
    # against opponent_metric.
    assert deltas.own == 0
    assert deltas.opponent == -2


def test_brace_critfail_lets_a_free_hit_land():
    deltas = resolve_tier_deltas(
        kind=BeatKind.brace, base=2, outcome=RollOutcome.CritFail,
        overrides=None, target_tag=None,
    )
    assert deltas.own == 0
    assert deltas.opponent == 1


def test_brace_critsuccess_grants_counter_stance_fleeting_tag():
    deltas = resolve_tier_deltas(
        kind=BeatKind.brace, base=2, outcome=RollOutcome.CritSuccess,
        overrides=None, target_tag=None,
    )
    assert deltas.opponent == -2
    assert deltas.grants_fleeting_tag == "Counter Stance"


# ---- push defaults -------------------------------------------------------

def test_push_success_resolves_encounter():
    deltas = resolve_tier_deltas(
        kind=BeatKind.push, base=1, outcome=RollOutcome.Success,
        overrides=None, target_tag=None,
    )
    assert deltas.resolution is True


def test_push_tie_does_not_resolve():
    deltas = resolve_tier_deltas(
        kind=BeatKind.push, base=1, outcome=RollOutcome.Tie,
        overrides=None, target_tag=None,
    )
    assert deltas.resolution is False


def test_push_critfail_backslides_own_by_one():
    deltas = resolve_tier_deltas(
        kind=BeatKind.push, base=1, outcome=RollOutcome.CritFail,
        overrides=None, target_tag=None,
    )
    assert deltas.own == -1


def test_push_critsuccess_clean_exit_fleeting_tag():
    deltas = resolve_tier_deltas(
        kind=BeatKind.push, base=1, outcome=RollOutcome.CritSuccess,
        overrides=None, target_tag=None,
    )
    assert deltas.resolution is True
    assert deltas.grants_fleeting_tag == "Clean Exit"


# ---- angle defaults ------------------------------------------------------

def test_angle_success_grants_persistent_tag_leverage_one():
    deltas = resolve_tier_deltas(
        kind=BeatKind.angle, base=0, outcome=RollOutcome.Success,
        overrides=None, target_tag="Off-Balance",
    )
    assert deltas.grants_tag == "Off-Balance"
    assert deltas.tag_leverage == 1


def test_angle_critsuccess_grants_persistent_tag_leverage_two():
    deltas = resolve_tier_deltas(
        kind=BeatKind.angle, base=0, outcome=RollOutcome.CritSuccess,
        overrides=None, target_tag="Off-Balance",
    )
    assert deltas.grants_tag == "Off-Balance"
    assert deltas.tag_leverage == 2


def test_angle_tie_grants_fleeting_tag():
    deltas = resolve_tier_deltas(
        kind=BeatKind.angle, base=0, outcome=RollOutcome.Tie,
        overrides=None, target_tag="Off-Balance",
    )
    assert deltas.grants_fleeting_tag == "Off-Balance"


def test_angle_critfail_backfires_target_tag_onto_opponent():
    deltas = resolve_tier_deltas(
        kind=BeatKind.angle, base=0, outcome=RollOutcome.CritFail,
        overrides=None, target_tag="Off-Balance",
    )
    assert deltas.tag_backfire is True
    # text reused: the angle backfires onto the actor.
    assert deltas.grants_fleeting_tag == "Off-Balance"


def test_angle_requires_target_tag():
    with pytest.raises(ValueError):
        resolve_tier_deltas(
            kind=BeatKind.angle, base=0, outcome=RollOutcome.Success,
            overrides=None, target_tag=None,
        )


# ---- per-tier overrides --------------------------------------------------

def test_per_tier_override_replaces_default():
    overrides = {RollOutcome.CritFail: {"own": -2}}
    deltas = resolve_tier_deltas(
        kind=BeatKind.strike, base=4, outcome=RollOutcome.CritFail,
        overrides=overrides, target_tag=None,
    )
    assert deltas.own == -2
    # other tiers still use kind defaults
    success = resolve_tier_deltas(
        kind=BeatKind.strike, base=4, outcome=RollOutcome.Success,
        overrides=overrides, target_tag=None,
    )
    assert success.own == 4


def test_default_deltas_table_covers_all_kinds_and_tiers():
    tiers = {
        RollOutcome.CritFail, RollOutcome.Fail, RollOutcome.Tie,
        RollOutcome.Success, RollOutcome.CritSuccess,
    }
    for kind in BeatKind:
        assert set(DEFAULT_DELTAS[kind].keys()) == tiers


# ---- raise-path coverage --------------------------------------------------

def test_unknown_outcome_raises():
    with pytest.raises(ValueError, match="Unknown"):
        resolve_tier_deltas(
            kind=BeatKind.strike, base=3, outcome=RollOutcome.Unknown,
            overrides=None, target_tag=None,
        )


def test_eval_expr_rejects_unknown_form():
    from sidequest.game.beat_kinds import _eval_expr

    with pytest.raises(ValueError, match="unsupported delta expression"):
        _eval_expr("b * 2", base=3)
