"""Targeting bias under taunt — enemies prefer the taunting actor.

Spec: docs/superpowers/specs/2026-05-10-class-mechanical-surface-design.md §8.

These tests exercise ``select_enemy_target`` from
``sidequest.game.enemy_targeting``.  The function is forward-scaffolding
(not yet called from a mechanical enemy-turn driver) but the contract is
pinned here so that when enemy AI is introduced, breaking the taunt bias
turns red immediately.
"""

from __future__ import annotations

import random

import pytest

from sidequest.game.enemy_targeting import select_enemy_target


def test_enemy_targets_taunter_when_taunt_active(taunt_test_encounter):
    """With taunt active, all 10 simulated enemy target picks return the Fighter."""
    enc = taunt_test_encounter.enc
    fighter_id = taunt_test_encounter.fighter_id
    cleric_id = taunt_test_encounter.cleric_id

    enc.taunt.activate(actor_id=fighter_id)

    rng = random.Random(42)
    targets = [
        select_enemy_target(
            encounter=enc,
            allies=[fighter_id, cleric_id],
            rng=rng,
        )
        for _ in range(10)
    ]

    assert all(t == fighter_id for t in targets), (
        f"Expected all 10 enemy targets = {fighter_id!r}, got {targets}"
    )


def test_enemy_targeting_unbiased_without_taunt(taunt_test_encounter):
    """With no taunt, enemy targeting hits both allies across many trials."""
    enc = taunt_test_encounter.enc
    fighter_id = taunt_test_encounter.fighter_id
    cleric_id = taunt_test_encounter.cleric_id

    # Confirm taunt is inactive (default state)
    assert enc.taunt.active_actor is None

    rng = random.Random(42)
    targets = [
        select_enemy_target(
            encounter=enc,
            allies=[fighter_id, cleric_id],
            rng=rng,
        )
        for _ in range(50)
    ]

    assert fighter_id in targets and cleric_id in targets, (
        f"Without taunt, both allies should appear as targets across 50 trials. "
        f"Got: {set(targets)}"
    )
