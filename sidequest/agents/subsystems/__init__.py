"""Subsystem package — Local DM dispatch consumers.

Each subsystem is an async callable that takes a SubsystemDispatch (and
optionally additional state) and returns a SubsystemOutput. The Task 7
registry maps subsystem names to callables and runs the dispatch bank.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sidequest.protocol.dispatch import NarratorDirective


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


__all__ = ["SubsystemOutput"]
