import pytest
import yaml
from pydantic import ValidationError

from sidequest.genre.models.rules import BeatDef, ConfrontationDef


def _conf_yaml(beats_yaml: str, *, two_dials: bool = True) -> str:
    metric_block = (
        (
            "player_metric:\n"
            "  name: momentum\n"
            "  starting: 0\n"
            "  threshold: 10\n"
            "opponent_metric:\n"
            "  name: momentum\n"
            "  starting: 0\n"
            "  threshold: 10\n"
        )
        if two_dials
        else (
            "metric:\n"
            "  name: momentum\n"
            "  direction: bidirectional\n"
            "  starting: 0\n"
            "  threshold_high: 10\n"
            "  threshold_low: -10\n"
        )
    )
    return f"type: combat\nlabel: Test Combat\ncategory: combat\n{metric_block}beats:\n{beats_yaml}"


def test_beat_def_kind_required():
    raw = yaml.safe_load("id: attack\nlabel: Attack\nkind: strike\nbase: 2\nstat_check: STR\n")
    beat = BeatDef.model_validate(raw)
    assert beat.kind.value == "strike"
    assert beat.base == 2


def test_beat_def_invalid_kind_rejected():
    raw = yaml.safe_load("id: x\nlabel: X\nkind: bogus\nbase: 1\nstat_check: STR\n")
    with pytest.raises(ValidationError):
        BeatDef.model_validate(raw)


def test_beat_def_per_tier_override_parses():
    raw = yaml.safe_load("""
id: shield_bash
label: Shield Bash
kind: strike
base: 4
deltas:
  crit_fail:
    own: -2
  crit_success:
    own: 4
    grants_tag: "Off-Balance"
stat_check: STR
""")
    beat = BeatDef.model_validate(raw)
    assert beat.deltas is not None
    assert beat.deltas["crit_fail"]["own"] == -2
    assert beat.deltas["crit_success"]["grants_tag"] == "Off-Balance"


def test_angle_beat_requires_target_tag():
    raw = yaml.safe_load("id: feint\nlabel: Feint\nkind: angle\nstat_check: DEX\n")
    with pytest.raises(ValidationError):
        BeatDef.model_validate(raw)


def test_angle_beat_with_target_tag_ok():
    raw = yaml.safe_load(
        'id: feint\nlabel: Feint\nkind: angle\nstat_check: DEX\ntarget_tag: "Out of Position"\n'
    )
    beat = BeatDef.model_validate(raw)
    assert beat.target_tag == "Out of Position"


def test_confrontation_def_two_dials_loads():
    src = _conf_yaml(
        "  - id: attack\n    label: Attack\n    kind: strike\n    base: 2\n    stat_check: STR\n",
        two_dials=True,
    )
    cdef = ConfrontationDef.model_validate(yaml.safe_load(src))
    assert cdef.player_metric.threshold == 10
    assert cdef.opponent_metric.threshold == 10


def test_confrontation_def_legacy_single_metric_rejected():
    src = _conf_yaml(
        "  - id: attack\n    label: Attack\n    kind: strike\n    base: 2\n    stat_check: STR\n",
        two_dials=False,
    )
    with pytest.raises(ValidationError) as exc:
        ConfrontationDef.model_validate(yaml.safe_load(src))
    # Loud rejection per CLAUDE.md "no silent fallbacks".
    msg = str(exc.value)
    assert "metric" in msg or "player_metric" in msg


def test_legacy_metric_delta_field_rejected():
    raw = yaml.safe_load(
        "id: attack\nlabel: Attack\nkind: strike\nbase: 2\nstat_check: STR\nmetric_delta: 2\n"
    )
    with pytest.raises(ValidationError):
        BeatDef.model_validate(raw)


def test_failure_metric_delta_field_rejected():
    raw = yaml.safe_load(
        "id: shield_bash\nlabel: Shield Bash\nkind: strike\nbase: 4\n"
        "stat_check: STR\nfailure_metric_delta: -2\n"
    )
    with pytest.raises(ValidationError):
        BeatDef.model_validate(raw)
