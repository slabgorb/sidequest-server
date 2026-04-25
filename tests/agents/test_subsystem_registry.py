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
async def test_run_dispatch_bank_subsystem_exception_is_caught(minimal_npc_registry):
    """A subsystem raising inside the bank logs and continues."""
    # npc_agency raises ValueError when params.npc_name is missing —
    # use that as the raising subsystem rather than distinctive_detail_hint
    # (which now degrades to a no-op rather than raising).
    d = _make_dispatch("npc_agency", "k_err", params={})  # no npc_name
    pkg = _make_package([[d]])
    res = await run_dispatch_bank(pkg, context={"npc_registry": minimal_npc_registry})
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
async def test_run_dispatch_bank_filters_context_per_subsystem_signature(
    minimal_npc_registry,
):
    """Bank forwards only the kwargs each subsystem declares.

    `run_distinctive_detail` accepts only ``dispatch`` — without per-callable
    filtering, blasting ``npc_registry`` into it would raise
    ``TypeError: unexpected keyword argument 'npc_registry'``.
    """
    d_npc = _make_dispatch(
        "npc_agency", "knpc",
        params={"npc_name": "Harlan", "situation": "spotted"},
    )
    d_dd = _make_dispatch(
        "distinctive_detail_hint", "kdd",
        params={"target": "npc:goblin", "hint": "broken tooth"},
    )
    pkg = _make_package([[d_npc], [d_dd]])
    res = await run_dispatch_bank(
        pkg, context={"npc_registry": minimal_npc_registry},
    )
    # Both subsystems ran; neither raised.
    assert res.errors == []
    assert "knpc" in res.outputs_by_key
    assert "kdd" in res.outputs_by_key


@pytest.mark.asyncio
async def test_run_dispatch_bank_passes_empty_npc_registry_when_orchestrator_has_none():
    """`run_npc_agency` requires ``npc_registry`` even when empty —
    orchestrator now passes ``[]`` instead of omitting the kwarg, so the
    subsystem invokes without TypeError and degrades cleanly to
    ``npc_not_registered``."""
    d = _make_dispatch(
        "npc_agency", "k_empty",
        params={"npc_name": "Stranger", "situation": "spotted"},
    )
    pkg = _make_package([[d]])
    res = await run_dispatch_bank(pkg, context={"npc_registry": []})
    # Subsystem ran without raising; data['error'] surfaces the lookup miss.
    assert res.errors == []
    out = res.outputs_by_key["k_empty"]
    assert out.data["error"] == "npc_not_registered"


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
async def test_run_dispatch_bank_cycle_in_depends_on_records_bank_error(otel_capture):
    """A cycle in depends_on records a __bank__ error; zero dispatches run;
    authored directives still flow; the bank span carries an error attribute
    so the GM panel distinguishes cycle-aborted turns from empty ones."""
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

    # Bank span records the cycle-abort reason so GM panel can filter it.
    bank_spans = [s for s in otel_capture.get_finished_spans() if s.name == "local_dm.dispatch_bank"]
    assert len(bank_spans) == 1
    assert dict(bank_spans[0].attributes or {}).get("error") == "topo_sort_failure"


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


@pytest.mark.asyncio
async def test_run_dispatch_bank_emits_bank_and_subsystem_spans(otel_capture):
    """run_dispatch_bank emits one local_dm.dispatch_bank span and one
    local_dm.subsystem span per dispatch. Sebastien's lie detector: an
    absent span == the subsystem never ran, no matter what the narrator says."""
    a = _make_dispatch("reflect_absence", "k1")
    b = _make_dispatch(
        "distinctive_detail_hint", "k2",
        params={"target": "npc:goblin_2", "hint": "broken tooth"},
    )
    pkg = _make_package([[a, b]])

    res = await run_dispatch_bank(pkg)
    # Sanity — bank did the real work.
    assert "k1" in res.outputs_by_key
    assert "k2" in res.outputs_by_key

    spans = otel_capture.get_finished_spans()
    bank_spans = [s for s in spans if s.name == "local_dm.dispatch_bank"]
    sub_spans = [s for s in spans if s.name == "local_dm.subsystem"]

    assert len(bank_spans) == 1
    bank_attrs = dict(bank_spans[0].attributes or {})
    assert bank_attrs["turn_id"] == "t"
    assert bank_attrs["dispatch_count"] == 2

    # One subsystem span per dispatch, with correct name + key.
    assert len(sub_spans) == 2
    by_key = {dict(s.attributes or {})["idempotency_key"]: s for s in sub_spans}
    assert set(by_key.keys()) == {"k1", "k2"}

    k1_attrs = dict(by_key["k1"].attributes or {})
    assert k1_attrs["subsystem"] == "reflect_absence"
    assert isinstance(k1_attrs["produced_directives"], int)
    assert k1_attrs["produced_directives"] >= 0

    k2_attrs = dict(by_key["k2"].attributes or {})
    assert k2_attrs["subsystem"] == "distinctive_detail_hint"
    assert isinstance(k2_attrs["produced_directives"], int)
    assert k2_attrs["produced_directives"] >= 0


@pytest.mark.asyncio
async def test_run_dispatch_bank_subsystem_span_records_error(
    otel_capture, minimal_npc_registry,
):
    """When a subsystem raises, its local_dm.subsystem span records the
    error type and produced_directives=0 — no clean span for a broken run."""
    # npc_agency raises ValueError when npc_name is missing.
    d = _make_dispatch("npc_agency", "k_err", params={})
    pkg = _make_package([[d]])
    res = await run_dispatch_bank(pkg, context={"npc_registry": minimal_npc_registry})
    assert len(res.errors) == 1

    spans = otel_capture.get_finished_spans()
    sub_spans = [s for s in spans if s.name == "local_dm.subsystem"]
    assert len(sub_spans) == 1
    attrs = dict(sub_spans[0].attributes or {})
    assert attrs["subsystem"] == "npc_agency"
    assert attrs["produced_directives"] == 0
    assert "error" in attrs


@pytest.mark.asyncio
async def test_run_dispatch_bank_span_fires_on_empty_package(otel_capture):
    """Bank span fires even with zero dispatches, so absent parent span
    means the bank executor never ran — not "no dispatches this turn"."""
    pkg = DispatchPackage(
        turn_id="t-empty",
        per_player=[],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
        degraded_reason=None,
    )
    await run_dispatch_bank(pkg)
    spans = otel_capture.get_finished_spans()
    bank_spans = [s for s in spans if s.name == "local_dm.dispatch_bank"]
    assert len(bank_spans) == 1
    assert dict(bank_spans[0].attributes or {})["dispatch_count"] == 0
