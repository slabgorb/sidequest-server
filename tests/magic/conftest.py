"""Magic test fixtures.

Importing ``sidequest.magic.plugins`` triggers the side-effect registration
of every shipped plugin into ``MAGIC_PLUGINS``. Tests need that side effect
before calling ``get_plugin``. Hoist it into a session-scoped autouse fixture
so individual test bodies don't have to repeat the import.
"""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    WorldKnowledge,
    WorldMagicConfig,
)


@pytest.fixture(autouse=True, scope="session")
def _populate_magic_plugins_registry():
    import sidequest.magic.plugins  # noqa: F401


@pytest.fixture()
def world_config() -> WorldMagicConfig:
    """Full Coyote Reach config — two character bars + one world bar.

    This is the canonical test config shared across Tasks 1.x–3.x.
    Individual test modules may define a local ``world_config`` fixture
    with a trimmed shape when they need isolation (pytest local wins).
    """
    return WorldMagicConfig(
        world_slug="coyote_reach",
        genre_slug="space_opera",
        allowed_sources=["innate", "item_based"],
        active_plugins=["innate_v1", "item_legacy_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[HardLimit(id="psionics_never_decisive", description="x")],
        cost_types=["sanity", "notice", "vitality"],
        ledger_bars=[
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                threshold_low=0.40,
                consequence_on_low_cross="auto-fire The Bleeding-Through",
                starts_at_chargen=1.0,
            ),
            LedgerBarSpec(
                id="notice",
                scope="character",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.75,
                consequence_on_high_cross="auto-fire The Quiet Word",
                starts_at_chargen=0.0,
            ),
            LedgerBarSpec(
                id="hegemony_heat",
                scope="world",
                direction="up",
                range=(0.0, 1.0),
                threshold_high=0.70,
                consequence_on_high_cross="escalation",
                decay_per_session=0.05,
                starts_at_chargen=0.30,
            ),
        ],
        narrator_register="x",
    )
