"""Pure dice resolver tests — port of sidequest-game/src/dice.rs tests.

Physics-is-the-roll path (ADR-074 / story 34-12): the server uses
client-reported face values as authoritative. These tests cover the crit
rules, DC comparisons, modifier arithmetic, and the validation boundary.
"""
from __future__ import annotations

import pytest

from sidequest.game.dice import (
    EmptyPool,
    FaceCountMismatch,
    FaceOutOfRange,
    UnknownDie,
    generate_dice_seed,
    resolve_dice_with_faces,
)
from sidequest.protocol.dice import DieSides, DieSpec, RollOutcome


def d20(n: int = 1) -> DieSpec:
    return DieSpec(sides=DieSides.D20, count=n)


def d6(n: int) -> DieSpec:
    return DieSpec(sides=DieSides.D6, count=n)


class TestResolveDiceWithFaces:
    def test_success_above_dc(self) -> None:
        r = resolve_dice_with_faces([d20()], [14], 3, 15)
        assert r.total == 17
        assert r.outcome is RollOutcome.Success

    def test_success_exactly_at_dc(self) -> None:
        # total == difficulty → Tie (not Success, exclusive equality case)
        r = resolve_dice_with_faces([d20()], [12], 3, 15)
        assert r.total == 15
        assert r.outcome is RollOutcome.Tie

    def test_fail_below_dc(self) -> None:
        r = resolve_dice_with_faces([d20()], [5], 3, 15)
        assert r.total == 8
        assert r.outcome is RollOutcome.Fail

    def test_crit_success_on_nat_20_regardless_of_dc(self) -> None:
        # Huge negative modifier, trivial DC — CritSuccess still wins.
        r = resolve_dice_with_faces([d20()], [20], -100, 1)
        assert r.outcome is RollOutcome.CritSuccess

    def test_crit_fail_on_nat_1_regardless_of_modifier(self) -> None:
        # Huge positive modifier, trivial DC — CritFail still wins.
        r = resolve_dice_with_faces([d20()], [1], 100, 1)
        assert r.outcome is RollOutcome.CritFail

    def test_crit_success_wins_over_crit_fail_in_same_pool(self) -> None:
        # 2d20 with both 20 and 1 → CritSuccess (Keith 2026-04-11 rule).
        r = resolve_dice_with_faces([d20(2)], [20, 1], 0, 100)
        assert r.outcome is RollOutcome.CritSuccess

    def test_non_d20_never_triggers_crit(self) -> None:
        # 1d6 showing 6 is not a crit even if it's max face.
        r = resolve_dice_with_faces([d6(1)], [6], 0, 100)
        assert r.outcome is RollOutcome.Fail

    def test_mixed_pool_sums_correctly(self) -> None:
        # 1d20 + 3d6: faces [12, 4, 5, 6] + mod 2 → total 29
        # difficulty 27 means margin 2 (< 3 threshold) → Success
        r = resolve_dice_with_faces([d20(), d6(3)], [12, 4, 5, 6], 2, 27)
        assert r.total == 29
        assert r.outcome is RollOutcome.Success

    def test_negative_modifier_can_produce_negative_total(self) -> None:
        r = resolve_dice_with_faces([d20()], [1], -10, 1)
        # Crit fail dominates but total still computed.
        assert r.total == -9
        assert r.outcome is RollOutcome.CritFail

    def test_empty_pool_raises_empty_pool(self) -> None:
        with pytest.raises(EmptyPool):
            resolve_dice_with_faces([], [], 0, 10)

    def test_unknown_die_sides_raises_unknown_die(self) -> None:
        spec = DieSpec(sides=DieSides.Unknown, count=1)
        with pytest.raises(UnknownDie):
            resolve_dice_with_faces([spec], [3], 0, 10)

    def test_face_count_mismatch_raises(self) -> None:
        with pytest.raises(FaceCountMismatch):
            resolve_dice_with_faces([d20(2)], [15], 0, 10)

    def test_face_out_of_range_low(self) -> None:
        with pytest.raises(FaceOutOfRange):
            resolve_dice_with_faces([d20()], [0], 0, 10)

    def test_face_out_of_range_high(self) -> None:
        with pytest.raises(FaceOutOfRange):
            resolve_dice_with_faces([d20()], [21], 0, 10)


class TestGenerateDiceSeed:
    def test_deterministic_for_same_inputs(self) -> None:
        assert generate_dice_seed("s1", 5) == generate_dice_seed("s1", 5)

    def test_session_id_affects_seed(self) -> None:
        assert generate_dice_seed("s1", 5) != generate_dice_seed("s2", 5)

    def test_round_affects_seed(self) -> None:
        assert generate_dice_seed("s1", 5) != generate_dice_seed("s1", 6)

    def test_seed_fits_u64(self) -> None:
        seed = generate_dice_seed("session", 1000)
        assert 0 <= seed < (1 << 64)
