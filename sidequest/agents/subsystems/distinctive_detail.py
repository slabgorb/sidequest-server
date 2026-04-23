"""distinctive_detail_hint subsystem — spec §6.2.

When a referent is ambiguous, emit a narrator directive naming the chosen
target with a distinctive detail so the prose identifies it cleanly.
"""
from __future__ import annotations

from sidequest.agents.subsystems import SubsystemOutput
from sidequest.protocol.dispatch import NarratorDirective, SubsystemDispatch


async def run_distinctive_detail(dispatch: SubsystemDispatch) -> SubsystemOutput:
    """Emit a narrator directive naming the target referent by a distinctive detail."""
    target = dispatch.params.get("target")
    hint = dispatch.params.get("hint")
    if not target:
        raise ValueError("distinctive_detail_hint requires params.target")
    if not hint:
        raise ValueError("distinctive_detail_hint requires params.hint")

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
