"""reflect_absence subsystem — spec §6.3.

When a player addresses no one present, the narrator must describe the
absence honestly rather than invent a follower. This subsystem emits the
directive pair that enforces that.
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
