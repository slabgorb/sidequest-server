"""Tests for ``fixed_opponent_roll`` kwarg on resolve_opposed_check.

The save resolver pins the threat's d20 to the B26 table target via
this kwarg; existing combat callers omit the kwarg and the path is
identical to the prior behavior.
"""

import pytest

from sidequest.game.encounter import EncounterActor
from sidequest.game.opposed_check import resolve_opposed_check
from sidequest.protocol.dice import RollOutcome


class _FakeBeat:
    def __init__(self, stat_check: str = "STR") -> None:
        self.stat_check = stat_check


class _FakeCdef:
    def __init__(self, opponent_default_stats: dict[str, int]) -> None:
        self.opponent_default_stats = opponent_default_stats


def _player(stat: str = "STR", score: int = 12) -> EncounterActor:
    return EncounterActor(
        name="player",
        role="fighter",
        side="player",
        per_actor_state={"stats": {stat: score}},
    )


def _opp(stat: str = "STR", score: int = 10) -> EncounterActor:
    return EncounterActor(
        name="threat",
        role="monster",
        side="opponent",
        per_actor_state={"stats": {stat: score}},
    )


def test_fixed_opponent_roll_pins_opponent_d20():
    cdef = _FakeCdef({"STR": 10})
    res = resolve_opposed_check(
        player_actor=_player(score=12),
        opponent_actor=_opp(score=10),
        player_beat=_FakeBeat("STR"),
        opponent_beat=_FakeBeat("STR"),
        cdef=cdef,
        player_roll=15,
        opponent_roll=99,  # would be invalid if used; pinned value wins
        fixed_opponent_roll=14,
    )
    assert res.opponent_roll == 14
    assert res.shift == 2
    assert res.tier is RollOutcome.Success


def test_fixed_opponent_roll_validates_range():
    cdef = _FakeCdef({"STR": 10})
    with pytest.raises(ValueError, match="fixed_opponent_roll"):
        resolve_opposed_check(
            player_actor=_player(),
            opponent_actor=_opp(),
            player_beat=_FakeBeat(),
            opponent_beat=_FakeBeat(),
            cdef=cdef,
            player_roll=10,
            opponent_roll=10,
            fixed_opponent_roll=21,
        )


def test_fixed_opponent_roll_default_none_unchanged_path():
    cdef = _FakeCdef({"STR": 10})
    res = resolve_opposed_check(
        player_actor=_player(score=12),
        opponent_actor=_opp(score=10),
        player_beat=_FakeBeat("STR"),
        opponent_beat=_FakeBeat("STR"),
        cdef=cdef,
        player_roll=15,
        opponent_roll=10,
    )
    assert res.opponent_roll == 10
