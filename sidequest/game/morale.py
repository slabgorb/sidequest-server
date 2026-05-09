"""B/X morale check (2d6 vs MoraleDef.score).

Per spec docs/superpowers/specs/2026-05-08-cnc-bx-class-beats-morale-design.md §4.4.
Pure function — no side effects, no game-state mutation. Caller applies
the outcome (chase escalation / surrender / rout) per ConfrontationDef.morale.flee_consequence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from random import Random

from opentelemetry import trace

from sidequest.genre.models.rules import (
    ConfrontationDef,
    MoraleTrigger,
)
from sidequest.telemetry.spans.combat import SPAN_MORALE_CHECK

_tracer = trace.get_tracer(__name__)


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
    with _tracer.start_as_current_span(SPAN_MORALE_CHECK) as span:
        morale = confrontation.morale
        span.set_attribute("trigger", trigger.value)
        span.set_attribute("opponent_side_label", opponent_side.label)

        if morale is None or trigger not in morale.triggers:
            span.set_attribute("outcome", MoraleOutcome.stay.value)
            span.set_attribute("score", 0)
            span.set_attribute("total", 0)
            return MoraleOutcome.stay

        living = [o for o in opponent_side.opponents if o.alive]
        non_mindless = [o for o in living if not o.mindless]
        span.set_attribute("mindless_opponents_count", len(living) - len(non_mindless))
        if not non_mindless:
            span.set_attribute("outcome", MoraleOutcome.stay.value)
            span.set_attribute("score", morale.score)
            span.set_attribute("total", 0)
            return MoraleOutcome.stay

        d1, d2 = rng.randint(1, 6), rng.randint(1, 6)
        total = d1 + d2
        outcome = MoraleOutcome.stay if total <= morale.score else MoraleOutcome.flee
        span.set_attribute("score", morale.score)
        span.set_attribute("roll", f"{d1}+{d2}")
        span.set_attribute("total", total)
        span.set_attribute("outcome", outcome.value)
        span.set_attribute("flee_consequence", morale.flee_consequence.value)
        return outcome
