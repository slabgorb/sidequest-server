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
    model_config = {"extra": "forbid"}

    mandatory_outputs: list[str] = Field(min_length=1)
    optional_outputs: list[str] = Field(default_factory=list)


_BranchName = Literal["clear_win", "pyrrhic_win", "clear_loss", "refused"]


class ConfrontationDefinition(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    label: str
    plugin_tie_ins: list[str]
    auto_fire: bool = False
    auto_fire_trigger: str | None = None
    once_per_arc: bool = False
    rounds: int
    resource_pool: dict[str, str]
    description: str
    outcomes: dict[_BranchName, ConfrontationBranch]

    @field_validator("outcomes")
    @classmethod
    def all_four_branches_present(
        cls, outcomes: dict[_BranchName, ConfrontationBranch]
    ) -> dict[_BranchName, ConfrontationBranch]:
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
