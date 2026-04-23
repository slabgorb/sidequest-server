"""Tests for the subsystem registry and dispatch bank executor."""
from __future__ import annotations

import pytest

from sidequest.agents.subsystems import (
    SubsystemOutput,
    get_registered,
    run_dispatch_bank,
)
from sidequest.protocol.dispatch import (
    DispatchPackage,
    NarratorDirective,
    PlayerDispatch,
    SubsystemDispatch,
    VisibilityTag,
)


def _tag_all() -> VisibilityTag:
    return VisibilityTag(
        visible_to="all", perception_fidelity={}, secrets_for=[],
        redact_from_narrator_canonical=False,
    )


def _make_dispatch(name: str, key: str, *, depends_on=(), params=None) -> SubsystemDispatch:
    return SubsystemDispatch(
        subsystem=name,
        params=params or {},
        depends_on=list(depends_on),
        idempotency_key=key,
        visibility=_tag_all(),
    )


def _make_package(per_player_dispatches: list[list[SubsystemDispatch]]) -> DispatchPackage:
    return DispatchPackage(
        turn_id="t",
        per_player=[
            PlayerDispatch(
                player_id=f"player:P{i}",
                raw_action="",
                resolved=[],
                dispatch=dispatches,
                lethality=[],
                narrator_instructions=[],
            )
            for i, dispatches in enumerate(per_player_dispatches)
        ],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
        degraded_reason=None,
    )


def test_defaults_are_registered():
    registered = get_registered()
    assert {"reflect_absence", "distinctive_detail_hint", "npc_agency"} <= set(registered.keys())


@pytest.mark.asyncio
async def test_run_dispatch_bank_reflect_absence_produces_directives():
    pkg = _make_package([[_make_dispatch("reflect_absence", "k1")]])
    res = await run_dispatch_bank(pkg)
    kinds = {d.kind for d in res.directives}
    assert {"must_not_narrate", "must_narrate"} <= kinds
    assert "k1" in res.outputs_by_key


@pytest.mark.asyncio
async def test_run_dispatch_bank_unknown_subsystem_is_skipped():
    pkg = _make_package([[_make_dispatch("not_a_real_subsystem", "k1")]])
    res = await run_dispatch_bank(pkg)
    assert res.directives == []
    assert res.outputs_by_key == {}
    assert res.errors == []


@pytest.mark.asyncio
async def test_run_dispatch_bank_topo_sort_respects_depends_on():
    """B depends on A: execution order must be A, then B, even when declared reversed."""
    a = _make_dispatch("reflect_absence", "A")
    b = _make_dispatch("reflect_absence", "B", depends_on=["A"])
    pkg = _make_package([[b, a]])  # declared out of order
    res = await run_dispatch_bank(pkg)
    # Both ran, in the right order. outputs_by_key preserves insertion order.
    assert list(res.outputs_by_key.keys()) == ["A", "B"]


@pytest.mark.asyncio
async def test_run_dispatch_bank_directives_include_decomposer_authored():
    """Narrator_instructions authored directly by the decomposer (not via subsystem)
    still reach the final directive list."""
    pkg = DispatchPackage(
        turn_id="t",
        per_player=[PlayerDispatch(
            player_id="player:P0",
            raw_action="",
            resolved=[],
            dispatch=[],
            lethality=[],
            narrator_instructions=[NarratorDirective(
                kind="must_narrate", payload="a thing", visibility=_tag_all(),
            )],
        )],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
        degraded_reason=None,
    )
    res = await run_dispatch_bank(pkg)
    payloads = [d.payload for d in res.directives]
    assert "a thing" in payloads


@pytest.mark.asyncio
async def test_run_dispatch_bank_subsystem_exception_is_caught():
    """A subsystem raising inside the bank logs and continues."""
    d = _make_dispatch("distinctive_detail_hint", "k_err", params={"hint": "x"})  # no target
    pkg = _make_package([[d]])
    res = await run_dispatch_bank(pkg)
    assert len(res.errors) == 1
    assert res.errors[0][0] == "k_err"
    # No directives from the failing subsystem.
    assert res.directives == []


@pytest.mark.asyncio
async def test_run_dispatch_bank_threads_context_to_subsystems(minimal_npc_registry):
    """npc_agency receives npc_registry via context kwargs."""
    d = _make_dispatch(
        "npc_agency", "k1",
        params={"npc_name": "Harlan", "situation": "spotted"},
    )
    pkg = _make_package([[d]])
    res = await run_dispatch_bank(pkg, context={"npc_registry": minimal_npc_registry})
    out: SubsystemOutput = res.outputs_by_key["k1"]
    assert out.data["role"] == "innkeeper"


@pytest.mark.asyncio
async def test_run_dispatch_bank_empty_package_still_returns_authored_directives():
    """Package with zero dispatches but authored narrator_instructions — directives still flow."""
    pkg = DispatchPackage(
        turn_id="t",
        per_player=[PlayerDispatch(
            player_id="p",
            raw_action="",
            resolved=[],
            dispatch=[],
            lethality=[],
            narrator_instructions=[NarratorDirective(
                kind="must_narrate", payload="lone directive", visibility=_tag_all(),
            )],
        )],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
        degraded_reason=None,
    )
    res = await run_dispatch_bank(pkg)
    assert any(d.payload == "lone directive" for d in res.directives)


def test_register_subsystem_rejects_duplicate_name():
    """register_subsystem raises ValueError on duplicate registration
    (bare register — not the _register_defaults pop-before-insert path)."""
    from sidequest.agents.subsystems import register_subsystem

    async def noop(dispatch, **ctx) -> SubsystemOutput:
        return SubsystemOutput()

    # reflect_absence is already registered by _register_defaults.
    with pytest.raises(ValueError, match="already registered"):
        register_subsystem("reflect_absence", noop)


@pytest.mark.asyncio
async def test_run_dispatch_bank_cycle_in_depends_on_records_bank_error():
    """A cycle in depends_on records a __bank__ error; zero dispatches run;
    authored directives still flow."""
    a = _make_dispatch("reflect_absence", "A", depends_on=["B"])
    b = _make_dispatch("reflect_absence", "B", depends_on=["A"])
    pkg = _make_package([[a, b]])
    # Add an authored directive so we can confirm it still flows.
    pkg.per_player[0].narrator_instructions = [NarratorDirective(
        kind="must_narrate", payload="authored despite cycle", visibility=_tag_all(),
    )]

    res = await run_dispatch_bank(pkg)

    # Bank-level error recorded.
    bank_errors = [e for e in res.errors if e[0] == "__bank__"]
    assert len(bank_errors) == 1
    assert "cycle" in bank_errors[0][1].lower()

    # Zero subsystem dispatches ran.
    assert res.outputs_by_key == {}

    # Authored directive still flows.
    assert any(d.payload == "authored despite cycle" for d in res.directives)


@pytest.mark.asyncio
async def test_run_dispatch_bank_dangling_depends_on_is_ignored():
    """depends_on referencing a key not in this bank is treated as a no-op
    (decomposer may reference cross-turn dependencies we don't resolve here)."""
    a = _make_dispatch("reflect_absence", "A", depends_on=["does-not-exist"])
    pkg = _make_package([[a]])
    res = await run_dispatch_bank(pkg)
    # A still ran — dangling dep did not block it.
    assert "A" in res.outputs_by_key
    assert res.errors == []
