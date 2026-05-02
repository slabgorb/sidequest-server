"""reflect_absence — DORMANT.

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


async def run_reflect_absence(dispatch: SubsystemDispatch) -> SubsystemOutput:
    """Return directives forcing honest-absence narration."""
    tag = dispatch.visibility
    return SubsystemOutput(
        directives=[
            NarratorDirective(
                kind="must_not_narrate",
                payload="inventing an NPC follower or off-screen responder",
                visibility=tag,
            ),
            NarratorDirective(
                kind="must_narrate",
                payload="the empty room answering back — the absence itself is the scene",
                visibility=tag,
            ),
        ],
        data={},
    )


__all__ = ["run_reflect_absence"]
