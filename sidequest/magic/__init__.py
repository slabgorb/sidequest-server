"""Magic-system runtime — plugins, ledger bars, validator.

See docs/design/magic-taxonomy.md for the framework.
See docs/superpowers/specs/2026-04-28-magic-system-coyote-reach-implementation-design.md
for the v1 implementation scope.
"""
from sidequest.magic.models import (
    Flag,
    FlagSeverity,
    HardLimit,
    LedgerBarSpec,
    MagicWorking,
    Plugin,
    WorldKnowledge,
    WorldMagicConfig,
)

__all__ = [
    "Flag",
    "FlagSeverity",
    "HardLimit",
    "LedgerBarSpec",
    "MagicWorking",
    "Plugin",
    "WorldKnowledge",
    "WorldMagicConfig",
]
