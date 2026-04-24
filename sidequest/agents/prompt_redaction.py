"""Structural hiding — strips redact_from_narrator_canonical entries from a
DispatchPackage BEFORE it enters the narrator prompt.

Primary defense in Group G's two-layer redaction model. The narrator cannot
leak what it never saw; this module is what ensures it never sees it.

Paired with the OTEL leak-audit (sidequest.telemetry.leak_audit, Task 7)
which verifies this module's output is actually reflected in canonical prose.
"""
from __future__ import annotations

from opentelemetry import trace

from sidequest.protocol.dispatch import (
    DispatchPackage,
    LethalityVerdict,
    NarratorDirective,
    PlayerDispatch,
    SubsystemDispatch,
)

_tracer = trace.get_tracer("sidequest.prompt_redaction")


def redact_dispatch_package(
    pkg: DispatchPackage,
) -> tuple[DispatchPackage, list[SubsystemDispatch | NarratorDirective | LethalityVerdict]]:
    """Return (pkg_without_redacted_entries, list_of_removed_entries).

    Called by the narrator before prompt assembly. Removed entries are
    returned so the caller can route them to SECRET_NOTE channels (Task 6).
    """
    removed: list[SubsystemDispatch | NarratorDirective | LethalityVerdict] = []
    new_players: list[PlayerDispatch] = []

    for pd in pkg.per_player:
        kept_dispatch = []
        for d in pd.dispatch:
            if d.visibility.redact_from_narrator_canonical:
                removed.append(d)
            else:
                kept_dispatch.append(d)
        kept_directives = []
        for n in pd.narrator_instructions:
            if n.visibility.redact_from_narrator_canonical:
                removed.append(n)
            else:
                kept_directives.append(n)
        # LethalityVerdict does not carry a VisibilityTag in the current
        # protocol shape — the decomposer spec has it emitting via the
        # sibling SubsystemDispatch. If that changes, add a branch here.
        new_players.append(
            pd.model_copy(update={
                "dispatch": kept_dispatch,
                "narrator_instructions": kept_directives,
            })
        )

    if removed:
        with _tracer.start_as_current_span("prompt.redaction.structural") as span:
            span.set_attribute("turn_id", pkg.turn_id)
            span.set_attribute("redacted_count", len(removed))
            span.set_attribute(
                "redacted_kinds",
                [type(r).__name__ for r in removed],
            )
            span.set_attribute(
                "redacted_idempotency_keys",
                [r.idempotency_key for r in removed if isinstance(r, SubsystemDispatch)],
            )

    redacted_pkg = pkg.model_copy(update={"per_player": new_players})
    return redacted_pkg, removed
