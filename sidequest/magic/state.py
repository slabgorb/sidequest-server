"""MagicState aggregate — ledger registry, working log, applied via apply_working().

Stored as a pydantic field on GameSnapshot. Serializes via model_dump for
SQLite persistence. Mutator surface: apply_working, add_character,
set_bar_value (testing), tick_session_decay (Phase 6).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from sidequest.magic.confrontations import ConfrontationDefinition
from sidequest.magic.models import LedgerBarSpec, MagicWorking, WorldMagicConfig

_log = logging.getLogger(__name__)


class BarKey(BaseModel):
    """Compound key into the ledger registry."""

    model_config = {"extra": "forbid", "frozen": True}

    scope: Literal["character", "world", "item", "faction", "location", "bond_pair"]
    owner_id: str
    bar_id: str

    def __hash__(self) -> int:  # frozen=True provides this; explicit for clarity
        return hash((self.scope, self.owner_id, self.bar_id))


class LedgerBar(BaseModel):
    """A single ledger bar instance — value + spec reference."""

    model_config = {"extra": "forbid"}

    spec: LedgerBarSpec
    value: float


class WorkingRecord(BaseModel):
    """One historical magic working entry."""

    model_config = {"extra": "forbid"}

    plugin: str
    mechanism: str
    actor: str
    costs: dict[str, float]
    domain: str
    narrator_basis: str
    flavor: str | None = None
    consent_state: str | None = None
    item_id: str | None = None
    alignment_with_item_nature: float | None = None


class ThresholdCrossingEvent(BaseModel):
    """Returned by apply_working when a threshold crosses."""

    model_config = {"extra": "forbid"}

    bar_key: BarKey
    direction: Literal["up", "down"]
    consequence: str
    new_value: float


class ApplyWorkingResult(BaseModel):
    """Outcome of MagicState.apply_working()."""

    model_config = {"extra": "forbid"}

    working: WorkingRecord
    crossings: list[ThresholdCrossingEvent] = Field(default_factory=list)
    bar_changes: dict[str, tuple[float, float]] = Field(default_factory=dict)


def _serialize_bar_key(k: BarKey) -> str:
    """Serialize BarKey to a string for dict-key safe pydantic dump."""
    return f"{k.scope}|{k.owner_id}|{k.bar_id}"


def _deserialize_bar_key(s: str) -> BarKey:
    scope, owner_id, bar_id = s.split("|", 2)
    return BarKey(scope=scope, owner_id=owner_id, bar_id=bar_id)


class MagicState(BaseModel):
    """Aggregate magic state for a session.

    Persists alongside GameSnapshot. Field on GameSnapshot is
    `magic_state: MagicState | None`.
    """

    model_config = {"extra": "forbid"}

    # Frozen reference to the world's magic config.
    config: WorldMagicConfig
    # Ledger registry. Dict-key serialized for json compat.
    ledger: dict[str, LedgerBar] = Field(default_factory=dict)
    working_log: list[WorkingRecord] = Field(default_factory=list)
    # Phase 5 (Story 47-3): named confrontations loaded from
    # ``worlds/<world>/confrontations.yaml`` at session init. The
    # auto-fire evaluator + outcome dispatcher both read this list.
    confrontations: list[ConfrontationDefinition] = Field(default_factory=list)
    # Phase 5: per-actor innate ``control_tier`` counter, bumped by the
    # ``control_tier_advance`` mandatory output. Stored on MagicState
    # rather than the character sheet for v1 — see plan §5.4.
    control_tier: dict[str, int] = Field(default_factory=dict)
    # Phase 5: queue of status promotions emitted by mandatory_outputs
    # (status_add_scratch / status_add_wound / status_add_scar). The
    # narration_apply pipeline drains this list into ``Character.core.statuses``
    # at outcome resolution; storing it here keeps ``apply_mandatory_outputs``
    # observable in MagicState dumps without coupling it to a specific
    # snapshot character lookup.
    # S5 — Transient promotion queue. Drained by
    # ``narration_apply._drain_pending_status_promotions`` within the same
    # apply pass that ``magic.outputs._queue_status_promotion`` populates
    # it. ``exclude=True`` keeps it out of ``MagicState.model_dump`` so a
    # save mid-handler cannot persist a partial queue. Re-initializes
    # empty on load.
    pending_status_promotions: list[dict[str, str]] = Field(
        default_factory=list, exclude=True
    )

    @classmethod
    def from_config(cls, config: WorldMagicConfig) -> MagicState:
        """Construct empty MagicState; world-scope bars instantiated immediately."""
        state = cls(config=config)
        # Eagerly instantiate world-scope bars (per spec D1 = eager).
        for spec in config.ledger_bars:
            if spec.scope == "world":
                key = BarKey(scope="world", owner_id=config.world_slug, bar_id=spec.id)
                state.ledger[_serialize_bar_key(key)] = LedgerBar(
                    spec=spec, value=spec.starts_at_chargen
                )
        return state

    def add_character(self, character_id: str) -> None:
        """Instantiate per-character bars for `character_id`."""
        for spec in self.config.ledger_bars:
            if spec.scope == "character":
                key = BarKey(scope="character", owner_id=character_id, bar_id=spec.id)
                serialized = _serialize_bar_key(key)
                if serialized in self.ledger:
                    continue  # idempotent
                self.ledger[serialized] = LedgerBar(spec=spec, value=spec.starts_at_chargen)

    def add_item(
        self,
        item_id: str,
        *,
        bond_template: LedgerBarSpec | None = None,
        history_template: LedgerBarSpec | None = None,
    ) -> None:
        """Instantiate per-item bars (called when an item enters play)."""
        for template in (bond_template, history_template):
            if template is None:
                continue
            key = BarKey(scope="item", owner_id=item_id, bar_id=template.id)
            self.ledger[_serialize_bar_key(key)] = LedgerBar(
                spec=template, value=template.starts_at_chargen
            )

    def get_bar(self, key: BarKey) -> LedgerBar:
        return self.ledger[_serialize_bar_key(key)]

    def set_bar_value(self, key: BarKey, value: float) -> None:
        """Direct bar set — used by tests and pre-prompt context restoration."""
        bar = self.ledger[_serialize_bar_key(key)]
        bar.value = self._clamp(value, bar.spec)

    def apply_working(self, working: MagicWorking) -> ApplyWorkingResult:
        """Apply costs to actor's bars and detect threshold crossings.

        Raises KeyError if `actor` has no instantiated character bars.
        """
        # Confirm actor exists for at least one character bar (sanity check).
        # Prefix scan instead of full BarKey deserialization — both are O(n)
        # in the ledger, but startswith avoids constructing a BarKey per row.
        actor_prefix = f"character|{working.actor}|"
        if not any(k.startswith(actor_prefix) for k in self.ledger):
            raise KeyError(f"unknown actor: {working.actor!r}; call add_character first")

        record = WorkingRecord(**working.model_dump())
        crossings: list[ThresholdCrossingEvent] = []
        bar_changes: dict[str, tuple[float, float]] = {}

        for cost_type, amount in working.costs.items():
            key = BarKey(scope="character", owner_id=working.actor, bar_id=cost_type)
            serialized = _serialize_bar_key(key)
            if serialized not in self.ledger:
                # World-scope and item-scope cost propagation are wired in
                # later iterations. Until then, costs targeting non-character
                # bars are skipped — but the skip MUST be auditable per
                # CLAUDE.md "GM panel is the lie detector". Task 3.5 will
                # promote this to an OTEL `magic.unrouted_cost` span; until
                # then a structured warning makes the skip visible in logs.
                _log.warning(
                    "magic.unrouted_cost actor=%s cost_type=%s amount=%s "
                    "(no character-scope bar; world/item scope routing pending)",
                    working.actor,
                    cost_type,
                    amount,
                )
                continue
            bar = self.ledger[serialized]
            prev = bar.value
            new_value = self._clamp(prev - amount, bar.spec)
            # Notice rises *up* with cost — direction-aware:
            if bar.spec.direction == "up":
                new_value = self._clamp(prev + amount, bar.spec)
            bar.value = new_value
            bar_changes[cost_type] = (prev, new_value)

            # Threshold detection
            if (
                bar.spec.direction == "down"
                and bar.spec.threshold_low is not None
                and prev > bar.spec.threshold_low >= new_value
            ):
                crossings.append(
                    ThresholdCrossingEvent(
                        bar_key=key,
                        direction="down",
                        consequence=bar.spec.consequence_on_low_cross or "",
                        new_value=new_value,
                    )
                )
            elif (
                bar.spec.direction == "up"
                and bar.spec.threshold_high is not None
                and prev < bar.spec.threshold_high <= new_value
            ):
                crossings.append(
                    ThresholdCrossingEvent(
                        bar_key=key,
                        direction="up",
                        consequence=bar.spec.consequence_on_high_cross or "",
                        new_value=new_value,
                    )
                )
            # Bidirectional: handled in Phase 5 when bond/item_history bars
            # land. For now, character-scope bars are monotonic up or down.

        self.working_log.append(record)
        return ApplyWorkingResult(working=record, crossings=crossings, bar_changes=bar_changes)

    @staticmethod
    def _clamp(value: float, spec: LedgerBarSpec) -> float:
        lo, hi = spec.range
        return max(lo, min(hi, value))
