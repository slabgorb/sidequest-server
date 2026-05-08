"""learned_v1 — prepared-spell magic for caster classes (Mage, Cleric).

Loose-Vancian: known list per actor, daily preparation up to per-level
slots, cast = expended until rest. Slot bookkeeping rides the standard
LedgerBar registry (slots_l1, slots_l2, ...). Known/prepared lists ride
MagicState.known_spells / MagicState.prepared_spells.

Importing this module is the public API: it mutates MAGIC_PLUGINS by
side effect.
"""

from __future__ import annotations

from pathlib import Path

import yaml

__all__: list[str] = []

from sidequest.magic.models import (
    Flag,
    FlagSeverity,
    MagicWorking,
    Plugin,
    WorldMagicConfig,
)
from sidequest.magic.plugin import MAGIC_PLUGINS

_YAML_PATH = Path(__file__).with_suffix(".yaml")
descriptor: Plugin = Plugin.model_validate(yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")))

# Mechanisms that belong to other plugins. learned_v1 firing with these
# is a lane violation (mirror of item_legacy_v1's `native` rejection).
_ITEM_LANE_MECHANISMS: set[str] = {
    "discovery",
    "mccoy",
    "relational",
    "faction",
}
_INNATE_LANE_MECHANISMS: set[str] = {"native", "condition"}


class LearnedV1Plugin:
    plugin_id = "learned_v1"

    def required_attrs(self) -> set[str]:
        return set(descriptor.required_span_attrs)

    def validate_working(self, working: MagicWorking, config: WorldMagicConfig) -> list[Flag]:
        flags: list[Flag] = []

        if working.spell_id is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_spell_id",
                    detail="learned_v1 requires spell_id",
                )
            )
        if working.slot_level is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_slot_level",
                    detail="learned_v1 requires slot_level",
                )
            )
        elif working.slot_level < 1:
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="slot_level_below_one",
                    detail=f"slot_level must be >= 1, got {working.slot_level}",
                )
            )

        if working.mechanism in _ITEM_LANE_MECHANISMS:
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="learned_via_item_mechanism_is_lane_violation",
                    detail=(
                        f"mechanism {working.mechanism!r} is item_legacy_v1 territory; "
                        "learned_v1 must use studied or granted"
                    ),
                )
            )
        if working.mechanism in _INNATE_LANE_MECHANISMS:
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="learned_via_innate_mechanism_is_lane_violation",
                    detail=(
                        f"mechanism {working.mechanism!r} is innate_v1 territory; "
                        "learned_v1 must use studied or granted"
                    ),
                )
            )

        return flags


MAGIC_PLUGINS["learned_v1"] = LearnedV1Plugin()
