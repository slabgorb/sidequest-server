"""Structured Encounter System — universal encounter engine (Python port).

Port of ``sidequest-api/crates/sidequest-game/src/encounter.rs`` (724 LOC) for
Epic 42 / ADR-082 Phase 3.

Generalises the old split ``CombatState`` / ``ChaseState`` into a single
YAML-declarable engine for standoffs, negotiations, net combat, ship combat,
and any future structured encounter type (ADR-033).

Key design — ported verbatim from the Rust source:

- String-keyed ``encounter_type`` replaces hardcoded enum
- :class:`EncounterMetric` replaces ``separation_distance``
- :class:`SecondaryStats` replaces ``RigStats``
- :class:`EncounterActor` replaces ``ChaseActor``

Porting discipline (epic 42 execution-strategy spec §2):
    Rust source file is the behavioural contract. Every Rust method becomes
    one Python method with the same name and semantics. No idiomatic
    rewrites.

Not in 42-1 scope (see session + ADR-082):

- Chase cinematography (Phase 4)
- Sealed-letter turn dispatcher lookup logic — ``per_actor_state`` shape is
  preserved; lookup stays in Rust for now
- OTEL watcher emission — state mutations land here, dispatch-side consumer
  lands in 42-4
- ``from_confrontation_def`` / ``apply_beat`` / ``format_encounter_context``
  — these lean on :mod:`sidequest.genre` confrontation definitions that land
  in story 42-2 and 42-3
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# RigType — colocated with encounter per AC2 test contract
# ---------------------------------------------------------------------------
#
# TEA's Dev-notes guidance: tests import ``RigType`` from
# :mod:`sidequest.game.encounter` directly. Colocating is the simplest path
# and matches the "convenience-constructor" surface — ``SecondaryStats.rig()``
# consumes a ``RigType`` and emits the exact rig-stat block Rust emits via
# ``RigStats::from_type``. The full ``chase_depth`` module (apply_damage,
# terrain, cinematography) is Phase 4 and will not land in 42-1.


class RigType(str, Enum):
    """Rig archetype determines base stats.

    Port of ``sidequest_game::chase_depth::RigType``.
    Variant names match Rust serde default (PascalCase, no renaming).
    """

    Interceptor = "Interceptor"
    WarRig = "WarRig"
    Bike = "Bike"
    Hauler = "Hauler"
    Frankenstein = "Frankenstein"

    def base_stats(self) -> tuple[int, int, int, int, int]:
        """Base stats for this archetype: ``(hp, speed, armor, maneuver, fuel)``.

        Values ported verbatim from ``RigType::base_stats``.
        """
        return _RIG_BASE_STATS[self]


_RIG_BASE_STATS: dict[RigType, tuple[int, int, int, int, int]] = {
    RigType.Interceptor: (15, 5, 1, 3, 8),
    RigType.WarRig: (30, 2, 5, 1, 12),
    RigType.Bike: (8, 4, 0, 5, 5),
    RigType.Hauler: (25, 2, 3, 1, 20),
    RigType.Frankenstein: (18, 3, 2, 3, 10),
}


def _rig_damage_tier_label(hp: int, max_hp: int) -> str:
    """Damage-tier label from HP percentage.

    Port of ``RigStats::damage_tier`` + ``Display for RigDamageTier``.
    Rust emits the uppercase label — here we return the same strings so
    fixtures match byte-for-byte.
    """
    if max_hp == 0:
        return "WRECK"
    pct = (hp / max_hp) * 100.0
    if pct <= 0.0:
        return "WRECK"
    if pct <= 25.0:
        return "SKELETON"
    if pct <= 50.0:
        return "FAILING"
    if pct <= 75.0:
        return "COSMETIC"
    return "PRISTINE"


# ---------------------------------------------------------------------------
# Enums — MetricDirection, EncounterPhase
# ---------------------------------------------------------------------------


class MetricDirection(str, Enum):
    """Direction a metric moves toward resolution.

    Port of ``sidequest_game::encounter::MetricDirection``.

    Rust enum is ``#[non_exhaustive]`` — future variants may land. Unknown
    serialized variants MUST raise ``ValidationError`` (no silent fallback
    per CLAUDE.md "No Silent Fallbacks").
    """

    Ascending = "Ascending"
    Descending = "Descending"
    Bidirectional = "Bidirectional"


class EncounterPhase(str, Enum):
    """Narrative arc phase for structured encounters.

    Port of ``sidequest_game::encounter::EncounterPhase``. Universal across
    all encounter types — the same dramatic shape as the old ``ChasePhase``
    but not locked to chase semantics.
    """

    Setup = "Setup"
    Opening = "Opening"
    Escalation = "Escalation"
    Climax = "Climax"
    Resolution = "Resolution"

    def drama_weight(self) -> float:
        """Drama weight for this phase (used by cinematography).

        Values ported verbatim from ``EncounterPhase::drama_weight``.
        """
        return _DRAMA_WEIGHTS[self]


_DRAMA_WEIGHTS: dict[EncounterPhase, float] = {
    EncounterPhase.Setup: 0.70,
    EncounterPhase.Opening: 0.75,
    EncounterPhase.Escalation: 0.80,
    EncounterPhase.Climax: 0.95,
    EncounterPhase.Resolution: 0.70,
}


# ---------------------------------------------------------------------------
# Value types — StatValue, SecondaryStats, EncounterActor, EncounterMetric
# ---------------------------------------------------------------------------


class StatValue(BaseModel):
    """A single stat in a secondary stats block.

    Port of ``sidequest_game::encounter::StatValue``.
    """

    model_config = {"extra": "forbid"}  # CLAUDE.md "No Silent Fallbacks"

    current: int
    max: int


class SecondaryStats(BaseModel):
    """Generic secondary stats block — generalises ``RigStats``.

    Port of ``sidequest_game::encounter::SecondaryStats``.

    String-keyed so genre packs can declare arbitrary stats: hp/fuel/speed/
    armor/maneuver for vehicles, shields/hull/engines for ships, focus/nerve
    for standoffs, etc.
    """

    model_config = {"extra": "forbid"}  # CLAUDE.md "No Silent Fallbacks"

    stats: dict[str, StatValue] = Field(default_factory=dict)
    damage_tier: str | None = None

    @classmethod
    def rig(cls, rig_type: RigType) -> SecondaryStats:
        """Build a :class:`SecondaryStats` block from a :class:`RigType`.

        Port of ``SecondaryStats::rig`` + ``SecondaryStats::from_rig_stats``
        collapsed — Rust splits them so callers holding a live ``RigStats``
        can pass it in, but the Python port does not expose ``RigStats``
        (deferred to Phase 4). Values match ``RigStats::from_type(rig_type)``
        byte-for-byte.
        """
        hp, speed, armor, maneuver, fuel = rig_type.base_stats()
        stats: dict[str, StatValue] = {
            "hp": StatValue(current=hp, max=hp),
            "speed": StatValue(current=speed, max=speed),
            "armor": StatValue(current=armor, max=armor),
            "maneuver": StatValue(current=maneuver, max=maneuver),
            "fuel": StatValue(current=fuel, max=fuel),
        }
        return cls(stats=stats, damage_tier=_rig_damage_tier_label(hp, hp))


class EncounterActor(BaseModel):
    """A character assigned to an encounter role.

    Port of ``sidequest_game::encounter::EncounterActor``.

    String-keyed roles replace the old ``ChaseRole`` enum — genre packs can
    declare arbitrary roles (driver, gunner, duelist, netrunner, ...).

    ``per_actor_state`` carries structured state for resolution modes that
    track per-pilot descriptors between turns (e.g., bearing, range, energy,
    gun_solution). Used by ``SealedLetterLookup`` confrontations (ADR-077).
    Lookup logic stays in Rust for Phase 3; Python preserves the shape.
    """

    model_config = {"extra": "forbid"}  # CLAUDE.md "No Silent Fallbacks"

    name: str
    role: str
    per_actor_state: dict[str, Any] = Field(default_factory=dict)


class EncounterMetric(BaseModel):
    """The primary metric being tracked in the encounter.

    Port of ``sidequest_game::encounter::EncounterMetric``.
    """

    model_config = {"extra": "forbid"}  # CLAUDE.md "No Silent Fallbacks"

    name: str
    current: int
    starting: int
    direction: MetricDirection
    threshold_high: int | None = None
    threshold_low: int | None = None


# ---------------------------------------------------------------------------
# StructuredEncounter — the universal encounter model
# ---------------------------------------------------------------------------


class StructuredEncounter(BaseModel):
    """A universal structured encounter — the generalisation of ``ChaseState``.

    Port of ``sidequest_game::encounter::StructuredEncounter``.

    One string-keyed ``encounter_type`` ("combat", "chase", "standoff",
    "negotiation", ...) replaces the old hardcoded per-type state structs.
    """

    model_config = {"extra": "forbid"}  # CLAUDE.md "No Silent Fallbacks"

    encounter_type: str
    metric: EncounterMetric
    beat: int = 0
    structured_phase: EncounterPhase | None = None
    secondary_stats: SecondaryStats | None = None
    actors: list[EncounterActor] = Field(default_factory=list)
    outcome: str | None = None
    resolved: bool = False
    mood_override: str | None = None
    narrator_hints: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def chase(
        cls,
        escape_threshold: float,  # noqa: ARG003 — preserved for Rust signature parity
        rig_type: RigType | None,
        goal: int,
    ) -> StructuredEncounter:
        """Create a chase-type encounter from the old ``ChaseState`` parameters.

        Port of ``StructuredEncounter::chase``.

        Maps chase semantics onto the generic encounter model:

        - ``separation_distance`` -> metric ``name="separation"``, Ascending
        - ``goal`` -> ``threshold_high``
        - ``rig`` -> :class:`SecondaryStats` via :meth:`SecondaryStats.rig`

        ``escape_threshold`` is accepted but unused (Rust signature parity);
        it will matter when chase cinematography lands in Phase 4.
        """
        secondary_stats = SecondaryStats.rig(rig_type) if rig_type is not None else None

        return cls(
            encounter_type="chase",
            metric=EncounterMetric(
                name="separation",
                current=0,
                starting=0,
                direction=MetricDirection.Ascending,
                threshold_high=goal,
                threshold_low=None,
            ),
            beat=0,
            structured_phase=EncounterPhase.Setup,
            secondary_stats=secondary_stats,
            actors=[],
            outcome=None,
            resolved=False,
            mood_override=None,
            narrator_hints=[],
        )

    @classmethod
    def combat(cls, combatants: list[str], hp: int) -> StructuredEncounter:
        """Create a combat-type encounter.

        Port of ``StructuredEncounter::combat``.

        Maps combat semantics onto the generic encounter model:

        - HP -> Descending metric (``threshold_low=0``)
        - ``combatants`` -> actors with role ``"combatant"``
        - Starts at beat 0 in :attr:`EncounterPhase.Setup`
        """
        actors = [
            EncounterActor(name=name, role="combatant", per_actor_state={})
            for name in combatants
        ]

        return cls(
            encounter_type="combat",
            metric=EncounterMetric(
                name="hp",
                current=hp,
                starting=hp,
                direction=MetricDirection.Descending,
                threshold_high=None,
                threshold_low=0,
            ),
            beat=0,
            structured_phase=EncounterPhase.Setup,
            secondary_stats=None,
            actors=actors,
            outcome=None,
            resolved=False,
            mood_override=None,
            narrator_hints=[],
        )

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def resolve_from_trope(self, trope_id: str) -> None:
        """Resolve this encounter because an associated trope completed.

        Port of ``StructuredEncounter::resolve_from_trope``.

        No-op if the encounter is already resolved. Sets the outcome to
        reference the completing trope so the GM panel can trace the
        resolution source.

        OTEL emission (``encounter.state.resolved_by_trope``) is **not**
        wired here — the dispatch-side consumer lands in story 42-4.
        """
        if self.resolved:
            return
        self.resolved = True
        self.structured_phase = EncounterPhase.Resolution
        self.outcome = f"resolved by trope completion: {trope_id}"
