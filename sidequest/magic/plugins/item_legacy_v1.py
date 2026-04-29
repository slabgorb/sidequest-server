"""item_legacy_v1 — items as agents, McCoy/discovery/relational delivery.

Importing this module is the public API: it mutates ``MAGIC_PLUGINS`` by
side effect. ``__all__ = []`` keeps the package star-import from leaking
this module's imports.
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
descriptor: Plugin = Plugin.model_validate(
    yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
)


class ItemLegacyV1Plugin:
    plugin_id = "item_legacy_v1"

    def required_attrs(self) -> set[str]:
        return set(descriptor.required_span_attrs)

    def validate_working(
        self, working: MagicWorking, config: WorldMagicConfig
    ) -> list[Flag]:
        flags: list[Flag] = []

        if working.item_id is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_item_id",
                    detail="item_legacy_v1 requires item_id",
                )
            )
        if working.alignment_with_item_nature is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_alignment_with_item_nature",
                    detail="item_legacy_v1 requires alignment_with_item_nature",
                )
            )
        elif not -1.0 <= working.alignment_with_item_nature <= 1.0:
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="alignment_out_of_range",
                    detail=(
                        f"alignment_with_item_nature must be in [-1.0, 1.0], "
                        f"got {working.alignment_with_item_nature}"
                    ),
                )
            )

        # Plugin-lane respect: item magic firing without an item is innate territory.
        if working.mechanism in {"native"}:
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="item_legacy_via_native_is_lane_violation",
                    detail="native delivery is innate_v1 territory; items must be carried/found/built",
                )
            )

        return flags


# Side-effect registration at module-import time (mirrors innate_v1.py).
MAGIC_PLUGINS["item_legacy_v1"] = ItemLegacyV1Plugin()
