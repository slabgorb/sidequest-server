"""innate_v1 — character-as-source magic.

Mechanics live here; content lives in innate_v1.yaml. Loader pairs them.

Importing this module is the public API: it mutates ``MAGIC_PLUGINS`` by
side effect. Nothing here is intended to be re-exported through the
``plugins`` package's star-import — ``__all__ = []`` keeps the namespace
clean as more plugins are added.
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

# Content descriptor — loaded once at import time.
descriptor: Plugin = Plugin.model_validate(yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")))


# Flavor → expected consent_state mapping (innate_v1 spec).
_CONSENT_BY_FLAVOR = {
    "acquired": "involuntary",
    "born_to_it": "involuntary",
    "trained_register": "willing",  # only when reflexive register surfaces
    "covenant_lineage": "involuntary",
}


class InnateV1Plugin:
    plugin_id = "innate_v1"

    def required_attrs(self) -> set[str]:
        return set(descriptor.required_span_attrs)

    def validate_working(self, working: MagicWorking, config: WorldMagicConfig) -> list[Flag]:
        flags: list[Flag] = []

        # 1. Required-attr presence
        if working.flavor is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_flavor",
                    detail="innate_v1 requires flavor",
                )
            )
        if working.consent_state is None:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="missing_required_attr_consent_state",
                    detail="innate_v1 requires consent_state",
                )
            )

        # 2. Flavor → consent_state coherence
        if working.flavor and working.consent_state:
            expected = _CONSENT_BY_FLAVOR.get(working.flavor)
            if expected and expected != working.consent_state:
                flags.append(
                    Flag(
                        severity=FlagSeverity.YELLOW,
                        reason="consent_state_flavor_mismatch",
                        detail=(
                            f"flavor={working.flavor!r} expects "
                            f"consent_state={expected!r}, got {working.consent_state!r}"
                        ),
                    )
                )

        # 3. Plugin-lane respect — innate cannot name an external answering entity
        # (that's bargained_for_v1's territory).
        if working.mechanism == "faction":
            flags.append(
                Flag(
                    severity=FlagSeverity.RED,
                    reason="innate_via_faction_is_lane_violation",
                    detail="faction-mediated magic is bargained_for_v1 or learned_v1",
                )
            )

        return flags


# Side-effect registration at module-import time. Mirrors the SPAN_ROUTES
# pattern in sidequest/telemetry/spans/_core.py: the act of importing this
# module IS registration; you cannot import without registering.
MAGIC_PLUGINS["innate_v1"] = InnateV1Plugin()
