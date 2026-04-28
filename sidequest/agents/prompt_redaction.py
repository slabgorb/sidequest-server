"""prompt_redaction — DORMANT.

This module is not invoked on the live turn path as of 2026-04-28
(see docs/superpowers/specs/2026-04-28-localdm-offline-only-design.md).

It is preserved for two consumers:
  1. The offline LocalDM corpus runner (follow-up story).
  2. Re-engagement on the live path once ADR-073's local fine-tuned
     router replaces the Haiku CLI subprocess.

Unit tests for this module remain in `just check-all` so it does not
bit-rot. If you find yourself adding a live caller, you are landing
ADR-073 (or undoing this design); update both ends.
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
