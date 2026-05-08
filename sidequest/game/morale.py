"""B/X morale check (2d6 vs MoraleDef.score).

Per spec docs/superpowers/specs/2026-05-08-cnc-bx-class-beats-morale-design.md §4.4.
Pure function — no side effects, no game-state mutation. Caller applies
the outcome (chase escalation / surrender / rout) per ConfrontationDef.morale.flee_consequence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from random import Random

from sidequest.genre.models.rules import (
    ConfrontationDef,
    MoraleTrigger,
)


class MoraleOutcome(StrEnum):
    stay = "stay"
    flee = "flee"


@dataclass(frozen=True)
class OpponentState:
    """Minimal per-opponent snapshot for morale evaluation."""

    id: str
    mindless: bool = False
    alive: bool = True
    is_leader: bool = False


@dataclass(frozen=True)
class OpponentSideState:
    label: str
    opponents: list[OpponentState] = field(default_factory=list)


def maybe_check_morale(
    confrontation: ConfrontationDef,
    opponent_side: OpponentSideState,
    trigger: MoraleTrigger,
    rng: Random,
) -> MoraleOutcome:
    """Roll 2d6 vs MoraleDef.score. Stay if total ≤ score; flee if >.

    No-op (Stay) if confrontation.morale is None or trigger not in morale.triggers.
    Sides composed entirely of mindless opponents always Stay (B/X canon — no
    Intelligence score, no morale check). Mixed sides roll once for the
    non-mindless cohort; mindless members keep fighting on a Flee outcome
    (handled by the caller, not this function).
    """
    morale = confrontation.morale
    if morale is None or trigger not in morale.triggers:
        return MoraleOutcome.stay

    living = [o for o in opponent_side.opponents if o.alive]
    non_mindless = [o for o in living if not o.mindless]
    if not non_mindless:
        return MoraleOutcome.stay

    total = rng.randint(1, 6) + rng.randint(1, 6)
    return MoraleOutcome.stay if total <= morale.score else MoraleOutcome.flee
