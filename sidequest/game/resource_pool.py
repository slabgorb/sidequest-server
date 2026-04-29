"""ResourcePool — generic named resource with thresholds (stories 16-10, 16-11).

A :class:`ResourcePool` tracks a numeric value within ``[min, max]``
bounds, with optional ``decay_per_turn``, voluntary spending control,
and threshold-based event detection. Threshold crossings mint
:class:`LoreFragment` instances for permanent narrator memory via
:func:`mint_threshold_lore`.

Engine-internal — all Pydantic types use ``extra='forbid'`` so malformed
save data fails loud per the project's no-silent-fallback rule.
:class:`ResourcePatchResult` is the single exception: it is not a
save-file surface, so it carries no ``extra='forbid'`` constraint.

The mutator surface lives on :class:`GameSnapshot` (see
:mod:`sidequest.game.session`), not on :class:`ResourcePool` itself —
``GameSnapshot`` owns ``apply_resource_patch``,
``apply_resource_patch_player``, ``apply_pool_decay``,
``init_resource_pools``, and the ``_with_lore`` convenience.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Re-export the threshold helpers so call sites can reach them via
# ``from sidequest.game.resource_pool import mint_threshold_lore``.
from sidequest.game.thresholds import detect_crossings, mint_threshold_lore


class ResourceThreshold(BaseModel):
    """A threshold that fires an event when the pool value crosses a boundary.

    direction = "down" (default, back-compat) — fires on downward crossing.
    direction = "up" — fires on upward crossing.

    Magic ledger bars use both directions: ``sanity`` fires down at 0.40,
    ``notice`` fires up at 0.75.  See :func:`detect_crossings`.
    """

    model_config = {"extra": "forbid"}

    at: float
    event_id: str
    narrator_hint: str
    direction: Literal["down", "up"] = "down"


class ResourcePatchOp(StrEnum):
    """Operation to apply to a resource pool.

    Variant member names are PascalCase (``Add``, ``Subtract``, ``Set``)
    but wire values are lowercase (``"add"``, ``"subtract"``, ``"set"``).
    """

    Add = "add"
    Subtract = "subtract"
    Set = "set"


class ResourcePatch(BaseModel):
    """A patch that modifies a single resource pool."""

    model_config = {"extra": "forbid"}

    resource_name: str
    operation: ResourcePatchOp
    value: float


class ResourcePatchError(Exception):
    """Base exception for resource-patch failures.

    Callers catch the base class to handle all variants.
    """


class UnknownResource(ResourcePatchError):
    """Raised when a patch targets a resource pool that does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown resource: {name}")
        self.name = name


class NotVoluntary(ResourcePatchError):
    """Raised when the player-path subtract hits a non-voluntary pool.

    The engine path (:meth:`GameSnapshot.apply_resource_patch`) bypasses
    this check; only the player path
    (:meth:`GameSnapshot.apply_resource_patch_player`) enforces the
    ``voluntary`` flag.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"resource '{name}' is not voluntary — player cannot spend it"
        )
        self.name = name


class ResourcePatchResult(BaseModel):
    """Result of applying a resource patch, including threshold crossings.

    Not a save-file surface. Pydantic for consistency with the rest of
    the module; no ``extra='forbid'``.
    """

    old_value: float
    new_value: float
    crossed_thresholds: list[ResourceThreshold] = Field(default_factory=list)


class ResourcePool(BaseModel):
    """A named resource pool with bounded numeric value and optional thresholds.

    Engine-internal, strict: ``extra='forbid'`` so malformed pool dicts
    fail loud per CLAUDE.md.

    The ``label`` field defaults to empty for back-compat with saves
    predating the field; :meth:`GameSnapshot.init_resource_pools`
    populates it from the genre pack declaration on first session load.
    """

    model_config = {"extra": "forbid"}

    name: str
    label: str = ""
    current: float
    min: float
    max: float
    voluntary: bool
    decay_per_turn: float
    thresholds: list[ResourceThreshold] = Field(default_factory=list)

    def _apply_and_clamp(
        self,
        op: ResourcePatchOp,
        value: float,
    ) -> ResourcePatchResult:
        """Apply a raw value change (unclamped delta or set), clamp, and
        detect threshold crossings.

        Private — public surfaces live on :class:`GameSnapshot`.
        """
        old_value = self.current
        if op is ResourcePatchOp.Add:
            raw = self.current + value
        elif op is ResourcePatchOp.Subtract:
            raw = self.current - value
        elif op is ResourcePatchOp.Set:
            raw = value
        else:  # pragma: no cover — StrEnum makes this unreachable
            raise ValueError(f"unknown ResourcePatchOp: {op!r}")

        # Clamp to [min, max].
        self.current = max(self.min, min(self.max, raw))
        crossed = detect_crossings(self.thresholds, old_value, self.current)
        return ResourcePatchResult(
            old_value=old_value,
            new_value=self.current,
            crossed_thresholds=crossed,
        )


__all__ = [
    "NotVoluntary",
    "ResourcePatch",
    "ResourcePatchError",
    "ResourcePatchOp",
    "ResourcePatchResult",
    "ResourcePool",
    "ResourceThreshold",
    "UnknownResource",
    # ``mint_threshold_lore`` is re-exported here as a convenience.
    # ``detect_crossings`` is NOT re-exported — it is structurally a
    # threshold-module helper, not a resource-pool primitive.
    # Package-level access is via ``sidequest.game.detect_crossings``.
    "mint_threshold_lore",
]
