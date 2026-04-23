"""Subsystem package — Local DM dispatch consumers.

Each subsystem is an async callable taking (dispatch: SubsystemDispatch,
**context) -> SubsystemOutput. The registry maps subsystem names to
callables. run_dispatch_bank executes a full DispatchPackage's worth
of dispatches, respecting depends_on and idempotency keys.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sidequest.protocol.dispatch import (
    DispatchPackage,
    NarratorDirective,
    SubsystemDispatch,
)

logger = logging.getLogger(__name__)

SubsystemCallable = Callable[..., Awaitable["SubsystemOutput"]]


@dataclass
class SubsystemOutput:
    """Output of one subsystem dispatch.

    Directives feed the narrator prompt. Data feeds downstream subsystems
    (e.g., Group C lethality reads npc_agency disposition from here) and
    the StatePatch phase.

    Convention: when a subsystem produces no useful output (e.g., looks up
    a missing entity), it returns ``directives=[]`` and ``data["error"]`` set
    to a short string code (e.g., ``"npc_not_registered"``). The bank
    executor (Task 7) does not raise on error-only outputs; it records them
    and continues. Subsystems MAY include additional diagnostic fields in
    data alongside the error code.
    """

    directives: list[NarratorDirective] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class BankResult:
    """Result of executing a DispatchPackage's subsystem bank."""

    directives: list[NarratorDirective] = field(default_factory=list)
    outputs_by_key: dict[str, SubsystemOutput] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# Registry populated at import time in _register_defaults().
_REGISTRY: dict[str, SubsystemCallable] = {}


def register_subsystem(name: str, fn: SubsystemCallable) -> None:
    if name in _REGISTRY:
        raise ValueError(f"subsystem already registered: {name}")
    _REGISTRY[name] = fn


def get_registered() -> dict[str, SubsystemCallable]:
    return dict(_REGISTRY)


def _register_defaults() -> None:
    from sidequest.agents.subsystems.distinctive_detail import run_distinctive_detail
    from sidequest.agents.subsystems.npc_agency import run_npc_agency
    from sidequest.agents.subsystems.reflect_absence import run_reflect_absence

    # Unregister-then-register to keep this import idempotent across test reloads.
    for name, fn in (
        ("reflect_absence", run_reflect_absence),
        ("distinctive_detail_hint", run_distinctive_detail),
        ("npc_agency", run_npc_agency),
    ):
        _REGISTRY.pop(name, None)
        _REGISTRY[name] = fn


_register_defaults()


def _topo_sort(dispatches: list[SubsystemDispatch]) -> list[SubsystemDispatch]:
    by_key = {d.idempotency_key: d for d in dispatches}
    order: list[SubsystemDispatch] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            raise ValueError(f"cycle in depends_on involving {key}")
        if key not in by_key:
            visited.add(key)
            return
        visiting.add(key)
        for dep in by_key[key].depends_on:
            visit(dep)
        visiting.remove(key)
        visited.add(key)
        order.append(by_key[key])

    for d in dispatches:
        visit(d.idempotency_key)
    return order


async def run_dispatch_bank(
    package: DispatchPackage,
    *,
    context: dict[str, Any] | None = None,
) -> BankResult:
    """Execute every SubsystemDispatch in the package.

    Runs sequentially in topological order. Unknown subsystems are logged
    and skipped. Exceptions are caught per-dispatch and logged; never
    re-raised.
    """
    context = context or {}
    result = BankResult()

    all_dispatches: list[SubsystemDispatch] = []
    for pd in package.per_player:
        all_dispatches.extend(pd.dispatch)
    # CrossAction has no narrator_instructions field (Group B; may extend in Group G).
    # Authored directives flow only through per_player[*].narrator_instructions.
    for ca in package.cross_player:
        all_dispatches.extend(ca.dispatch)

    if not all_dispatches:
        # Still include decomposer-authored narrator_instructions even when no
        # subsystem dispatches ran.
        for pd in package.per_player:
            result.directives.extend(pd.narrator_instructions)
        return result

    try:
        ordered = _topo_sort(all_dispatches)
    except ValueError as exc:
        logger.error("subsystems.bank_topo_sort_failed exc=%s", exc)
        result.errors.append(("__bank__", repr(exc)))
        # Authored directives still flow; zero subsystem dispatches run.
        for pd in package.per_player:
            result.directives.extend(pd.narrator_instructions)
        return result

    seen: set[str] = set()
    for d in ordered:
        if d.idempotency_key in seen:
            continue
        seen.add(d.idempotency_key)

        fn = _REGISTRY.get(d.subsystem)
        if fn is None:
            logger.warning(
                "subsystems.unknown subsystem=%s key=%s", d.subsystem, d.idempotency_key,
            )
            continue
        try:
            out = await fn(d, **context)
        except Exception as exc:
            logger.warning(
                "subsystems.dispatch_failed subsystem=%s key=%s exc=%s",
                d.subsystem, d.idempotency_key, exc,
            )
            result.errors.append((d.idempotency_key, repr(exc)))
            continue

        result.outputs_by_key[d.idempotency_key] = out
        result.directives.extend(out.directives)

    for pd in package.per_player:
        result.directives.extend(pd.narrator_instructions)

    return result


__all__ = [
    "BankResult",
    "SubsystemCallable",
    "SubsystemOutput",
    "get_registered",
    "register_subsystem",
    "run_dispatch_bank",
]
