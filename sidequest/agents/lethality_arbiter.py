"""LethalityArbiter — deterministic lethality synthesis (Group C).

Spec: docs/superpowers/specs/2026-04-23-local-dm-decomposer-design.md §4

Runs AFTER run_dispatch_bank and BEFORE narrator_directives registration.
Reads:
  - LethalityPolicy (loaded from the active genre pack)
  - Player-character cores (`pc_cores_by_player`)
  - NPC cores present in the scene (`npc_cores_by_name`)
  - BankResult (for future subsystems that emit `data["fatal_hit"]` etc.)

Phase A trigger is edge-based only: any core with `edge.current == 0` fires
the policy's `verdicts_on_zero_edge` entry. Confrontation-beat-failure and
resource-pool-depletion triggers land in Group E when the subsystems that
produce those signals exist on the Python port.

The arbiter is deterministic and synchronous — no LLM call. The decomposer
may still emit `LethalityVerdict` entries in `DispatchPackage.per_player[*].
lethality` for paper-trail purposes; arbiter output is authoritative on
conflict (see Task 8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sidequest.agents.subsystems import BankResult
from sidequest.game.creature_core import CreatureCore
from sidequest.genre.models.lethality import LethalityPolicy
from sidequest.protocol.dispatch import (
    DispatchPackage,
    LethalityVerdict,
    NarratorDirective,
    VisibilityTag,
)

logger = logging.getLogger(__name__)


@dataclass
class LethalityResult:
    """Arbiter output: authoritative verdicts + paired narrator directives."""

    verdicts: list[LethalityVerdict] = field(default_factory=list)
    directives: list[NarratorDirective] = field(default_factory=list)


class LethalityArbiter:
    """Synthesise lethality verdicts from post-bank state + genre policy."""

    def __init__(self, policy: LethalityPolicy) -> None:
        self._policy = policy

    def arbitrate(
        self,
        *,
        package: DispatchPackage,
        bank_result: BankResult,
        pc_cores_by_player: dict[str, CreatureCore],
        npc_cores_by_name: dict[str, CreatureCore],
    ) -> LethalityResult:
        result = LethalityResult()
        for player_id, core in pc_cores_by_player.items():
            if core.edge.current == 0:
                result.verdicts.append(self._build_verdict(
                    entity=f"player:{player_id}",
                    verdict_kind=self._policy.verdicts_on_zero_edge.pc,
                    cause=(
                        f"{core.name} reduced to zero edge "
                        f"(0/{core.edge.max})"
                    ),
                ))
        for npc_name, core in npc_cores_by_name.items():
            if core.edge.current == 0:
                result.verdicts.append(self._build_verdict(
                    entity=f"npc:{npc_name}",
                    verdict_kind=self._policy.verdicts_on_zero_edge.npc,
                    cause=(
                        f"{core.name} reduced to zero edge "
                        f"(0/{core.edge.max})"
                    ),
                ))
        return result

    def _build_verdict(
        self,
        *,
        entity: str,
        verdict_kind: str,
        cause: str,
    ) -> LethalityVerdict:
        policy = self._policy
        directive = (
            f"{entity} verdict={verdict_kind}. "
            f"{policy.must_narrate} "
            f"Do NOT: {policy.must_not_narrate}"
        )
        return LethalityVerdict(
            entity=entity,
            verdict=verdict_kind,  # type: ignore[arg-type]  # validated against Literal at ctor
            cause=cause,
            reversibility=policy.default_reversibility,
            narrator_directive=directive,
            soul_md_constraint=policy.soul_md_constraint,
            witness_scope={},  # Group G fills this in from VisibilityTag pipeline
        )


__all__ = ["LethalityArbiter", "LethalityResult"]
