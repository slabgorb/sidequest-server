"""distinctive_detail — DORMANT.

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

from sidequest.agents.subsystems import SubsystemOutput
from sidequest.protocol.dispatch import NarratorDirective, SubsystemDispatch


async def run_distinctive_detail(dispatch: SubsystemDispatch) -> SubsystemOutput:
    """Emit a narrator directive naming the target referent by a distinctive detail.

    The decomposer prompt instructs the LLM to provide both ``target`` and
    ``hint``, but LLM compliance is best-effort. When either is missing we
    degrade to a no-op (empty directives + ``data["error"]``) instead of
    raising — the bank surfaces ``error`` as a span attribute, so OTEL
    still flags the bad dispatch without spewing TypeError into the
    orchestrator log every turn.
    """
    target = dispatch.params.get("target")
    hint = dispatch.params.get("hint")
    if not target:
        return SubsystemOutput(
            directives=[],
            data={"error": "missing_params.target"},
        )
    if not hint:
        return SubsystemOutput(
            directives=[],
            data={"error": "missing_params.hint", "target": target},
        )

    return SubsystemOutput(
        directives=[
            NarratorDirective(
                kind="distinctive_detail_for_referent",
                payload=f"name {target} by its distinctive detail: {hint}",
                visibility=dispatch.visibility,
            ),
        ],
        data={},
    )


__all__ = ["run_distinctive_detail"]
