"""innate_v1 plugin behavior."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    FlagSeverity,
    HardLimit,
    MagicWorking,
    WorldKnowledge,
    WorldMagicConfig,
)
from sidequest.magic.plugin import get_plugin


@pytest.fixture
def world_config() -> WorldMagicConfig:
    return WorldMagicConfig(
        world_slug="coyote_reach",
        genre_slug="space_opera",
        allowed_sources=["innate", "item_based"],
        active_plugins=["innate_v1", "item_legacy_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[
            HardLimit(id="no_ftl_telepathy", description="psionics bound to local space"),
            HardLimit(id="psionics_never_decisive", description="weapons trump psionics"),
        ],
        cost_types=["sanity", "notice", "vitality"],
        ledger_bars=[],
        can_build_caster=False,
        can_build_item_user=True,
        narrator_register="The Reach doesn't perform miracles. It bleeds through.",
    )


def test_innate_v1_registered():
    # Importing plugins package populates registry
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    assert plugin.plugin_id == "innate_v1"


def test_innate_v1_required_attrs():
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    assert plugin.required_attrs() == {"flavor", "consent_state"}


def test_innate_v1_clean_working_no_flags(world_config):
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira Mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="alien-tech proximity",
        flavor="acquired",
        consent_state="involuntary",
    )
    flags = plugin.validate_working(working, world_config)
    assert flags == []


def test_innate_v1_missing_flavor_yellow_flag(world_config):
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira Mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        consent_state="involuntary",
    )
    flags = plugin.validate_working(working, world_config)
    assert len(flags) == 1
    assert flags[0].severity == FlagSeverity.YELLOW
    assert "flavor" in flags[0].reason


def test_innate_v1_missing_consent_state_yellow_flag(world_config):
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira Mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
    )
    flags = plugin.validate_working(working, world_config)
    assert any(f.severity == FlagSeverity.YELLOW and "consent_state" in f.reason for f in flags)


def test_innate_v1_consent_flavor_mismatch_yellow(world_config):
    """flavor=acquired implies consent_state=involuntary in innate_v1 spec."""
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira Mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="willing",  # mismatch
    )
    flags = plugin.validate_working(working, world_config)
    assert any(
        f.severity == FlagSeverity.YELLOW and "consent" in f.reason for f in flags
    )


def test_innate_v1_faction_mechanism_red_flag(world_config):
    """`faction` mechanism is bargained_for_v1's lane; flag RED."""
    import sidequest.magic.plugins  # noqa: F401

    plugin = get_plugin("innate_v1")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="faction",
        actor="Sira Mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    flags = plugin.validate_working(working, world_config)
    assert any(
        f.severity == FlagSeverity.RED and "lane_violation" in f.reason for f in flags
    )


def test_innate_v1_yaml_descriptor_loads():
    """The paired .yaml descriptor loads as a Plugin model."""
    from sidequest.magic.plugins.innate_v1 import descriptor

    assert descriptor.plugin_id == "innate_v1"
    assert descriptor.source == "innate"
    assert "condition" in descriptor.delivery_mechanisms
    assert "native" in descriptor.delivery_mechanisms
    assert descriptor.required_span_attrs == ["flavor", "consent_state"]
