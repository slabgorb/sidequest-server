"""Confrontation definitions for magic confrontations — Story 47-3.

Loads ``worlds/<w>/confrontations.yaml`` into a list of
``ConfrontationDefinition`` and evaluates ``auto_fire_trigger`` expressions
against per-character bar values.

Per design Decision #8 every outcome branch must produce >= 1
mandatory_output. Per design Decision #9 the four branches
(clear_win, pyrrhic_win, clear_loss, refused) are required.

Failure modes (CLAUDE.md no silent fallback):
    - Missing file → ConfrontationLoaderError
    - Malformed YAML → ConfrontationLoaderError
    - Missing branch → ConfrontationLoaderError
    - Empty mandatory_outputs in any branch → ConfrontationLoaderError
    - Malformed auto_fire_trigger expression → ValueError on evaluation
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfrontationLoaderError(RuntimeError):
    """Raised when confrontations.yaml fails to load or validate."""


class ConfrontationBranch(BaseModel):
    """One resolved outcome branch of a magic confrontation.

    ``mandatory_outputs`` fire unconditionally when the branch is the
    chosen resolution; ``optional_outputs`` are narrator hints that may
    or may not fire depending on prose. Per design Decision #8, every
    branch must declare at least one mandatory_output (enforced at
    pydantic validation time via ``min_length=1``).
    """

    model_config = {"extra": "forbid"}

    mandatory_outputs: list[str] = Field(min_length=1)
    optional_outputs: list[str] = Field(default_factory=list)


BranchName = Literal["clear_win", "pyrrhic_win", "clear_loss", "refused"]


class FireConditions(BaseModel):
    """Room/bond/cooldown gates for rig-coupled auto-fire (Story 47-4).

    Distinct from ``auto_fire_trigger`` (the bar-DSL evaluator that drives
    sanity/notice threshold crossings). ``fire_conditions`` is consulted
    by the room-entry hook in ``sidequest.game.room_movement``: a
    confrontation is eligible when the player enters a room matching
    ``interior_room_present`` on a chassis whose chassis-side bond_tier
    is at or above ``bond_tier_min`` and ``cooldown_turns`` have elapsed
    since the last firing.
    """

    model_config = {"extra": "forbid"}

    interior_room_present: str
    bond_tier_min: str
    cooldown_turns: int


class ConfrontationDefinition(BaseModel):
    """A named magic confrontation loaded from ``confrontations.yaml``.

    Each definition wires a confrontation id (e.g. ``the_bleeding_through``)
    to its plugin tie-ins, optional auto-fire trigger, outcome catalog, and
    resource pool. ``once_per_arc`` suppresses repeat firings within an
    arc; ``auto_fire`` plus ``auto_fire_trigger`` drives the
    threshold-crossing evaluator (``evaluate_auto_fire_triggers``);
    ``register=intimate`` plus ``fire_conditions`` drives the rig-coupled
    room-entry evaluator (``find_eligible_room_autofire``).

    The four-branch invariant (``clear_win``, ``pyrrhic_win``,
    ``clear_loss``, ``refused``) holds for the standard combat/social
    register. ``register="intimate"`` confrontations (e.g. ``the_tea_brew``)
    are by design two-branch (``clear_win`` / ``refused``) — the failure
    modes pyrrhic_win and clear_loss don't apply to consensual intimate
    rituals.
    """

    model_config = {"extra": "forbid"}

    id: str
    label: str
    plugin_tie_ins: list[str]
    register: str | None = None
    rig_tie_ins: list[str] = Field(default_factory=list)
    auto_fire: bool = False
    auto_fire_trigger: str | None = None
    fire_conditions: FireConditions | None = None
    once_per_arc: bool = False
    rounds: int
    resource_pool: dict[str, str]
    description: str
    outcomes: dict[BranchName, ConfrontationBranch]

    @field_validator("outcomes")
    @classmethod
    def required_branches_present(
        cls, outcomes: dict[BranchName, ConfrontationBranch], info
    ) -> dict[BranchName, ConfrontationBranch]:
        register = (info.data or {}).get("register")
        if register == "intimate":
            required = {"clear_win", "refused"}
        else:
            required = {"clear_win", "pyrrhic_win", "clear_loss", "refused"}
        missing = required - set(outcomes.keys())
        if missing:
            raise ValueError(f"missing branch(es): {sorted(missing)}")
        return outcomes


def load_confrontations(path: Path) -> list[ConfrontationDefinition]:
    """Load and validate a confrontations.yaml file.

    Returns the parsed list of ConfrontationDefinition. Raises
    ConfrontationLoaderError on any failure — no silent fallback.
    """
    if not path.exists():
        raise ConfrontationLoaderError(f"confrontations yaml not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfrontationLoaderError(f"yaml parse error: {path}: {e}") from e

    raw_list = data.get("confrontations", [])
    try:
        return [ConfrontationDefinition.model_validate(d) for d in raw_list]
    except ValidationError as e:
        raise ConfrontationLoaderError(f"schema error in {path}: {e}") from e


# Format: "<bar_id> <op> <value>" where op in [<=, >=, <, >, ==]
_TRIGGER_RE = re.compile(r"^\s*(\w+)\s*(<=|>=|<|>|==)\s*([\d.]+)\s*$")


def evaluate_auto_fire_triggers(
    *,
    confs: list[ConfrontationDefinition],
    character_id: str,
    bar_values: dict[str, float],
) -> list[tuple[ConfrontationDefinition, str]]:
    """Return (confrontation, character_id) pairs for triggers that match.

    ``bar_values`` is a dict of ``bar_id`` → current value for the actor.
    Confrontations without ``auto_fire`` or whose bar is not in
    ``bar_values`` produce no firing. Malformed trigger expressions
    raise ``ValueError`` — no silent fallback (CLAUDE.md).
    """
    fired: list[tuple[ConfrontationDefinition, str]] = []
    for c in confs:
        if not c.auto_fire or c.auto_fire_trigger is None:
            continue
        m = _TRIGGER_RE.match(c.auto_fire_trigger)
        if m is None:
            raise ValueError(
                f"cannot parse auto_fire_trigger {c.auto_fire_trigger!r} for {c.id}"
            )
        bar_id, op, value_str = m.groups()
        threshold = float(value_str)
        actual = bar_values.get(bar_id)
        if actual is None:
            continue
        if op == "<=":
            matched = actual <= threshold
        elif op == ">=":
            matched = actual >= threshold
        elif op == "<":
            matched = actual < threshold
        elif op == ">":
            matched = actual > threshold
        elif op == "==":
            matched = actual == threshold
        else:  # pragma: no cover — regex pinned to the five ops above
            raise ValueError(f"unsupported operator {op!r}")
        if matched:
            fired.append((c, character_id))
    return fired


# ---------------------------------------------------------------------------
# Story 47-4: rig-coupled room-entry auto-fire eligibility (the_tea_brew)
# ---------------------------------------------------------------------------

# Bond tier ladder ordered weakest → strongest. Index lookup gives ordinal
# comparison for ``bond_tier_min`` checks. Mirrors the ladder in
# ``sidequest.game.chassis._TIER_THRESHOLDS`` — kept duplicated here rather
# than imported so the magic module doesn't reach into game internals; if
# the ladder changes both must be updated (covered by tests).
_BOND_TIER_ORDER: tuple[str, ...] = (
    "severed",
    "hostile",
    "strained",
    "neutral",
    "familiar",
    "trusted",
    "fused",
)


def _bond_tier_at_or_above(actual: str, minimum: str) -> bool:
    if actual not in _BOND_TIER_ORDER:
        raise ValueError(f"unknown bond_tier {actual!r}")
    if minimum not in _BOND_TIER_ORDER:
        raise ValueError(f"unknown bond_tier_min {minimum!r}")
    return _BOND_TIER_ORDER.index(actual) >= _BOND_TIER_ORDER.index(minimum)


def find_eligible_room_autofire(
    *,
    confrontations: list[ConfrontationDefinition],
    room_local_id: str,
    bond_tier_chassis: str,
) -> list[ConfrontationDefinition]:
    """Filter ``confrontations`` to those whose ``fire_conditions`` match
    a player room-entry on the given chassis — *before* cooldown filtering.

    Eligible iff:
      * ``c.auto_fire`` and ``c.fire_conditions`` are set;
      * ``fire_conditions.interior_room_present == room_local_id``;
      * ``bond_tier_chassis >= fire_conditions.bond_tier_min``.

    Story 47-6: cooldown is NOT filtered here — the caller applies the
    cooldown gate so the OTEL ``room.entry_evaluated`` span can
    distinguish ``eligible_count`` (matched the room+bond+autofire
    predicate) from ``fired_count`` (matched AND off cooldown). Without
    this split, the GM panel can't tell "no confrontation matched" from
    "matched but on cooldown" — the playtest reproduction's whole point.

    Returns the ConfrontationDefinitions, in YAML order, in a list so the
    caller can dispatch each in turn (typical case is one — the slice's
    ``the_tea_brew`` — but multiple intimate-register confrontations could
    coexist on the same room in a future world).
    """
    eligible: list[ConfrontationDefinition] = []
    for c in confrontations:
        if not c.auto_fire or c.fire_conditions is None:
            continue
        fc = c.fire_conditions
        if fc.interior_room_present != room_local_id:
            continue
        if not _bond_tier_at_or_above(bond_tier_chassis, fc.bond_tier_min):
            continue
        eligible.append(c)
    return eligible
