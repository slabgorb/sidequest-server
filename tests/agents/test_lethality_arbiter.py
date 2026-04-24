"""LethalityArbiter — deterministic verdict synthesis from game state + policy.

Spec: docs/superpowers/specs/2026-04-23-local-dm-decomposer-design.md §4
Group C: verdict producer consumes HP/edge state + policy, emits verdicts
and paired narrator directives. Edge-based triggers only for Phase A.
"""
from __future__ import annotations

import pytest

from sidequest.agents.lethality_arbiter import LethalityArbiter, LethalityResult
from sidequest.agents.subsystems import BankResult
from sidequest.game.creature_core import CreatureCore, EdgePool, Inventory
from sidequest.genre.models.lethality import LethalityPolicy, VerdictsOnZeroEdge
from sidequest.protocol.dispatch import (
    DispatchPackage,
    PlayerDispatch,
    VisibilityTag,
)


def _make_pc(name: str, edge_current: int, edge_max: int = 10) -> CreatureCore:
    return CreatureCore(
        name=name,
        description="A PC.",
        personality="Brave.",
        level=1,
        xp=0,
        inventory=Inventory(),
        statuses=[],
        edge=EdgePool(current=edge_current, max=edge_max, base_max=edge_max),
    )


def _heavy_metal_policy() -> LethalityPolicy:
    return LethalityPolicy(
        genre_key="heavy_metal",
        default_reversibility="permanent",
        verdicts_on_zero_edge=VerdictsOnZeroEdge(pc="dead", npc="dead"),
        soul_md_constraint="genre_truth:lethal_for_this_genre",
        must_narrate="Render the death.",
        must_not_narrate="narrate survival; invent rescue",
    )


def _empty_package(turn_id: str = "turn-1", player_id: str = "alice") -> DispatchPackage:
    return DispatchPackage(
        turn_id=turn_id,
        per_player=[PlayerDispatch(player_id=player_id, raw_action="swing sword")],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
    )


def test_pc_at_zero_edge_produces_heavy_metal_dead_verdict():
    """Edge.current == 0 → policy.verdicts_on_zero_edge.pc → verdict emitted."""
    arbiter = LethalityArbiter(policy=_heavy_metal_policy())
    pc = _make_pc("Alice", edge_current=0)
    result = arbiter.arbitrate(
        package=_empty_package(player_id="alice"),
        bank_result=BankResult(),
        pc_cores_by_player={"alice": pc},
        npc_cores_by_name={},
    )
    assert isinstance(result, LethalityResult)
    assert len(result.verdicts) == 1
    v = result.verdicts[0]
    assert v.entity == "player:alice"
    assert v.verdict == "dead"
    assert v.reversibility == "permanent"
    assert v.soul_md_constraint == "genre_truth:lethal_for_this_genre"
    assert "Alice" in v.cause


def test_pc_above_zero_edge_produces_no_verdict():
    """Edge.current > 0 → arbiter emits nothing for that PC."""
    arbiter = LethalityArbiter(policy=_heavy_metal_policy())
    pc = _make_pc("Alice", edge_current=5)
    result = arbiter.arbitrate(
        package=_empty_package(player_id="alice"),
        bank_result=BankResult(),
        pc_cores_by_player={"alice": pc},
        npc_cores_by_name={},
    )
    assert result.verdicts == []
    assert result.directives == []
