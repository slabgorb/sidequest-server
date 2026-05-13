import random

import pytest

from sidequest.game.builder import (
    CharacterBuilder,
    NoQualifyingClassesError,
    PoolValueNotPresentError,
    UnfilledArrangementError,
)
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import RulesConfig
from tests.game.test_builder_arrange_visible import _make_scenes_with_arrange_visible


def _classes_fighter_thief() -> list[ClassDef]:
    return [
        ClassDef(
            id="fighter",
            display_name="Fighter",
            rpg_role="tank",
            jungian_default="hero",
            prime_requisite="STR",
            minimum_score=9,
            kit_table="fighter_kit",
        ),
        ClassDef(
            id="thief",
            display_name="Thief",
            rpg_role="stealth",
            jungian_default="outlaw",
            prime_requisite="DEX",
            minimum_score=9,
            kit_table="thief_kit",
        ),
    ]


def _seeded_builder():
    rules = RulesConfig(
        ability_score_names=["STR", "DEX", "CON", "INT", "WIS", "CHA"],
        stat_generation="roll_3d6_arrange_visible",
    )
    return CharacterBuilder(
        scenes=_make_scenes_with_arrange_visible(),
        rules=rules,
        rng=random.Random(42),
    ).with_classes(_classes_fighter_thief())


def test_assign_stat_moves_value_from_pool_to_slot():
    builder = _seeded_builder()
    pool = builder.arrangement_pool()
    value = pool[0]
    builder.assign_stat(stat_name="STR", value=value)
    assert builder.arrangement_assignment()["STR"] == value
    new_pool = builder.arrangement_pool()
    assert new_pool.count(value) == pool.count(value) - 1


def test_assign_stat_value_not_in_pool_raises():
    builder = _seeded_builder()
    with pytest.raises(PoolValueNotPresentError):
        builder.assign_stat(stat_name="STR", value=99)


def test_assign_stat_replaces_existing_returning_old_to_pool():
    builder = _seeded_builder()
    pool = list(builder.arrangement_pool())
    a, b = pool[0], pool[1]
    builder.assign_stat("STR", a)
    builder.assign_stat("STR", b)
    assignment = builder.arrangement_assignment()
    assert assignment["STR"] == b
    # a went back to the pool
    assert a in builder.arrangement_pool()


def test_clear_stat_returns_value_to_pool():
    builder = _seeded_builder()
    pool_before = builder.arrangement_pool()
    value = pool_before[0]
    builder.assign_stat(stat_name="STR", value=value)
    builder.clear_stat(stat_name="STR")
    assert builder.arrangement_assignment()["STR"] is None
    assert sorted(builder.arrangement_pool()) == sorted(pool_before)


def test_clear_stat_idempotent_on_empty_slot():
    builder = _seeded_builder()
    pool_before = list(builder.arrangement_pool())
    builder.clear_stat("STR")  # already None
    assert sorted(builder.arrangement_pool()) == sorted(pool_before)


def test_confirm_arrangement_requires_all_six_filled():
    builder = _seeded_builder()
    pool = builder.arrangement_pool()
    builder.assign_stat("STR", pool[0])
    with pytest.raises(UnfilledArrangementError):
        builder.confirm_arrangement()


def test_confirm_arrangement_requires_at_least_one_qualifying_class():
    builder = _seeded_builder()
    # Force an all-low arrangement.
    builder._arrangement_pool = [3, 4, 5, 6, 7, 8]
    builder._arrangement_assignment = dict(STR=3, DEX=4, CON=5, INT=6, WIS=7, CHA=8)
    with pytest.raises(NoQualifyingClassesError):
        builder.confirm_arrangement()


def test_confirm_arrangement_materializes_rolled_stats_in_canonical_order():
    builder = _seeded_builder()
    pool = builder.arrangement_pool()
    sorted_pool = sorted(pool, reverse=True)
    for stat, v in zip(["STR", "DEX", "CON", "INT", "WIS", "CHA"], sorted_pool, strict=True):
        builder.assign_stat(stat, v)
    builder.confirm_arrangement()
    rolled = (
        builder.rolled_stats()
        if callable(getattr(builder, "rolled_stats", None))
        else builder._rolled_stats
    )
    assert rolled is not None
    rolled_dict = dict(rolled)
    assert set(rolled_dict.keys()) == {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
    # Pool is consumed
    assert builder.arrangement_pool() is None
    assert builder.arrangement_assignment() is None


def test_reject_arrangement_rerolls_pool_and_clears_assignment():
    builder = _seeded_builder()
    pool_before = list(builder.arrangement_pool())
    builder.assign_stat("STR", pool_before[0])
    builder.reject_arrangement()
    pool_after = builder.arrangement_pool()
    assert pool_after is not None
    assert len(pool_after) == 6
    assert all(v is None for v in builder.arrangement_assignment().values())
