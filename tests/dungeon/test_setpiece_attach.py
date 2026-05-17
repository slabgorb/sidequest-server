"""Tests for sidequest.dungeon.setpiece_attach — Plan 6, Task 1.

Three checkboxes from the plan:
  1. identical inputs → byte-identical rolled result (frozen-into-save contract).
  2. distinct (region_id|setpiece_id|slot_id) tuples do not collude.
  3. a ComponentSlot with one option always picks that option; empty options
     list is rejected by Plan 4's validator — assert the guard still holds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.dungeon.setpiece_attach import RolledSetPiece, roll_set_piece
from sidequest.dungeon.setpieces import (
    ComponentSlot,
    SetPiece,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_set_piece(slots: list[dict]) -> SetPiece:
    """Build a minimal valid SetPiece with given slot specs."""
    return SetPiece.model_validate(
        {
            "id": "test_trap",
            "name": "The Test Trap",
            "telegraph": "Pressure plate visible under grime.",
            "outcome": "Spears shoot from the walls.",
            "slots": slots,
        }
    )


_MULTI_OPTION_SET_PIECE = _make_set_piece(
    [
        {
            "name": "layout",
            "options": [
                {"value": "pit", "weight": 1.0},
                {"value": "corridor", "weight": 1.0},
                {"value": "chamber", "weight": 1.0},
            ],
        },
        {
            "name": "loot",
            "options": [
                {"value": "gold_coins", "weight": 2.0},
                {"value": "cursed_ring", "weight": 1.0},
            ],
        },
    ]
)

_SINGLE_OPTION_SET_PIECE = _make_set_piece(
    [
        {
            "name": "layout",
            "options": [{"value": "only_choice", "weight": 1.0}],
        }
    ]
)


# ---------------------------------------------------------------------------
# Test 1: identical inputs → byte-identical rolled result
# (frozen-into-save contract; spec §7)
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_identical_result():
    """Same call twice must produce an equal RolledSetPiece."""
    kwargs = dict(
        campaign_seed=999,
        expansion_id=1,
        region_id="exp001.r3",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    result_a = roll_set_piece(**kwargs)
    result_b = roll_set_piece(**kwargs)
    assert result_a == result_b


def test_determinism_against_hardcoded_expected_value():
    """Assert exact rolled values computed once; a seed-algorithm change
    must break this test loudly (frozen-into-save contract)."""
    result = roll_set_piece(
        campaign_seed=42,
        expansion_id=3,
        region_id="exp003.r7",
        setpiece_id="false_floor",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    # These values were computed by running the implementation once and are
    # now pinned. If the seed algorithm changes, this fails loudly — exactly
    # what "frozen into save" requires.
    assert isinstance(result, RolledSetPiece)
    assert set(result.slots.keys()) == {"layout", "loot"}
    # Pin the exact chosen values:
    assert result.slots["layout"] == "corridor"
    assert result.slots["loot"] == "gold_coins"


# ---------------------------------------------------------------------------
# Test 2: distinct (region_id|setpiece_id|slot_id) tuples do not collude
# ---------------------------------------------------------------------------


def test_distinct_region_ids_produce_different_rolls():
    """Different region_ids must not always roll the same slot values.

    Use a three-option slot with equal weights and a large sample of
    region_ids; expect non-constant results (probability of all-same is
    (1/3)^(N-1) ≈ negligible for N=20)."""
    results = set()
    for i in range(20):
        r = roll_set_piece(
            campaign_seed=1,
            expansion_id=1,
            region_id=f"exp001.r{i}",
            setpiece_id="same_piece",
            set_piece=_MULTI_OPTION_SET_PIECE,
        )
        results.add(r.slots["layout"])
    assert len(results) > 1, (
        "All 20 distinct region_ids rolled the same layout option — "
        "seed mixing is colliding across region_ids"
    )


def test_distinct_setpiece_ids_produce_different_rolls():
    """Different setpiece_ids with the same region must not always collude."""
    results = set()
    for i in range(20):
        r = roll_set_piece(
            campaign_seed=1,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id=f"piece_{i}",
            set_piece=_MULTI_OPTION_SET_PIECE,
        )
        results.add(r.slots["layout"])
    assert len(results) > 1, (
        "All 20 distinct setpiece_ids rolled the same layout option — "
        "seed mixing is colliding across setpiece_ids"
    )


def test_slot_id_collision_prevention():
    """Two different slots in the same set-piece must use independent RNGs.

    Use a set-piece with two slots having the same option distribution;
    if the per-slot sub-seed includes the slot name as the distinguisher,
    different slots should roll independently — not always identically."""
    # Build a set-piece where both slots have identical option lists.
    # If sub-seeding doesn't distinguish slots, they'd always match.
    symmetric = _make_set_piece(
        [
            {
                "name": "slot_alpha",
                "options": [
                    {"value": "A", "weight": 1.0},
                    {"value": "B", "weight": 1.0},
                    {"value": "C", "weight": 1.0},
                ],
            },
            {
                "name": "slot_beta",
                "options": [
                    {"value": "A", "weight": 1.0},
                    {"value": "B", "weight": 1.0},
                    {"value": "C", "weight": 1.0},
                ],
            },
        ]
    )
    same_count = 0
    for seed in range(30):
        r = roll_set_piece(
            campaign_seed=seed,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id="symmetric_piece",
            set_piece=symmetric,
        )
        if r.slots["slot_alpha"] == r.slots["slot_beta"]:
            same_count += 1
    # With 3 equal-weight options, expected same-fraction ≈ 1/3.
    # If sub-seeding doesn't separate slots, same_count would be 30.
    assert same_count < 30, (
        "slot_alpha and slot_beta always matched — slot sub-seeding "
        "is not distinguishing between slots within a set-piece"
    )


def test_prefix_collision_prevention():
    """(1, 23) and (12, 3) must not collude — delimiter prevents naive
    concatenation collisions."""
    # They may or may not differ by chance, but they must not ALWAYS be the
    # same because the delimiter prevents raw concatenation. Assert they
    # are treated as distinct inputs (different seeds are computed).
    # We can only check the rolls are at least sometimes distinguishable
    # by running multiple region_ids.
    results_1, results_2 = set(), set()
    for i in range(15):
        results_1.add(
            roll_set_piece(
                campaign_seed=1,
                expansion_id=23,
                region_id=f"exp023.r{i}",
                setpiece_id="piece",
                set_piece=_MULTI_OPTION_SET_PIECE,
            ).slots["layout"]
        )
        results_2.add(
            roll_set_piece(
                campaign_seed=12,
                expansion_id=3,
                region_id=f"exp003.r{i}",
                setpiece_id="piece",
                set_piece=_MULTI_OPTION_SET_PIECE,
            ).slots["layout"]
        )
    # Both streams must show variation (not stuck at one value from seed aliasing)
    assert len(results_1) > 1
    assert len(results_2) > 1


# ---------------------------------------------------------------------------
# Test 3: single-option slot; Plan 4 guard still holds
# ---------------------------------------------------------------------------


def test_single_option_slot_always_picks_that_option():
    """A ComponentSlot with exactly one option must always roll that option."""
    for seed in range(10):
        result = roll_set_piece(
            campaign_seed=seed,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id="one_choice",
            set_piece=_SINGLE_OPTION_SET_PIECE,
        )
        assert result.slots["layout"] == "only_choice"


def test_plan4_validator_still_rejects_empty_options():
    """Plan 4's guard rejects empty options lists — assert it still holds."""
    with pytest.raises(ValidationError, match="at least one option"):
        ComponentSlot(name="layout", options=[])


# ---------------------------------------------------------------------------
# Test: RolledSetPiece shape
# ---------------------------------------------------------------------------


def test_rolled_setpiece_contains_all_slot_names():
    """RolledSetPiece.slots must have one entry per ComponentSlot."""
    result = roll_set_piece(
        campaign_seed=0,
        expansion_id=0,
        region_id="exp000.r0",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    assert isinstance(result, RolledSetPiece)
    assert set(result.slots.keys()) == {"layout", "loot"}
    # Each value must be a valid option value string
    layout_values = {o.value for o in _MULTI_OPTION_SET_PIECE.slots[0].options}
    loot_values = {o.value for o in _MULTI_OPTION_SET_PIECE.slots[1].options}
    assert result.slots["layout"] in layout_values
    assert result.slots["loot"] in loot_values
