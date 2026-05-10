"""Enemy target-selection helpers.

Centralises the logic for choosing which ally an enemy targets on its turn.
In V1 (L1 ship) the only bias is taunt: when a taunt is active and the
taunting actor is among the candidate allies, all enemy picks go to the
taunter.  Outside of that narrow case targeting is uniform-random over the
supplied ally list.

Spec: docs/superpowers/specs/2026-05-10-class-mechanical-surface-design.md §8.

Wiring status: **forward-scaffolding** — the function is not yet called from
a real enemy-beat code path because enemy beats are narrator-driven in the
current engine (no mechanical enemy AI).  The contract is pinned by tests in
``tests/game/test_taunt_targeting.py``; once a mechanical enemy-turn driver
exists it should import and call ``select_enemy_target`` for every target
pick.
"""

from __future__ import annotations

import random

from sidequest.game.encounter import StructuredEncounter


def select_enemy_target(
    *,
    encounter: StructuredEncounter,
    allies: list[str],
    rng: random.Random,
) -> str | None:
    """Return the name of the ally an enemy will target this beat.

    Rules (in priority order):
    1. If ``encounter.taunt.active_actor`` is set **and** is present in
       ``allies``, return the taunter unconditionally (full bias, L1 ship).
    2. Otherwise pick uniformly at random from ``allies``.
    3. If ``allies`` is empty, return ``None``.

    Args:
        encounter: The live ``StructuredEncounter`` (read for taunt state).
        allies:    Candidate ally IDs the enemy may target.  Caller is
                   responsible for filtering to non-withdrawn actors first.
        rng:       Seeded ``random.Random`` for deterministic tests.

    Returns:
        Actor ID of the chosen target, or ``None`` when ``allies`` is empty.
    """
    if not allies:
        return None

    # Spec §8 — taunt targeting bias: full bias at L1 ship.
    taunter = encounter.taunt.active_actor
    if taunter is not None and taunter in allies:
        return taunter

    return rng.choice(allies)
