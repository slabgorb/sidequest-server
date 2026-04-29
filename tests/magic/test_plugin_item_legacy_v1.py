"""item_legacy_v1 plugin behavior."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    FlagSeverity,
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
        hard_limits=[],
        cost_types=["sanity", "notice", "vitality"],
        ledger_bars=[],
        narrator_register="The Reach doesn't perform miracles.",
    )


def test_item_legacy_v1_registered():
    plugin = get_plugin("item_legacy_v1")
    assert plugin.plugin_id == "item_legacy_v1"


def test_item_legacy_v1_required_attrs():
    plugin = get_plugin("item_legacy_v1")
    assert plugin.required_attrs() == {"item_id", "alignment_with_item_nature"}


def test_item_legacy_v1_clean_working_no_flags(world_config):
    plugin = get_plugin("item_legacy_v1")
    working = MagicWorking(
        plugin="item_legacy_v1",
        mechanism="discovery",
        actor="Sira Mendes",
        costs={"notice": 0.10},
        domain="physical",
        narrator_basis="named-gun reflexive shot",
        item_id="lassiter",
        alignment_with_item_nature=0.85,
    )
    assert plugin.validate_working(working, world_config) == []


def test_item_legacy_v1_missing_item_id_yellow(world_config):
    plugin = get_plugin("item_legacy_v1")
    working = MagicWorking(
        plugin="item_legacy_v1",
        mechanism="discovery",
        actor="Sira Mendes",
        costs={"notice": 0.10},
        domain="physical",
        narrator_basis="x",
        alignment_with_item_nature=0.85,
    )
    flags = plugin.validate_working(working, world_config)
    assert any(f.severity == FlagSeverity.YELLOW and "item_id" in f.reason for f in flags)


def test_item_legacy_v1_missing_alignment_yellow(world_config):
    """Missing alignment_with_item_nature emits a YELLOW flag (and skips the
    range check via the elif chain in validate_working)."""
    plugin = get_plugin("item_legacy_v1")
    working = MagicWorking(
        plugin="item_legacy_v1",
        mechanism="discovery",
        actor="Sira Mendes",
        costs={"notice": 0.10},
        domain="physical",
        narrator_basis="x",
        item_id="lassiter",
    )
    flags = plugin.validate_working(working, world_config)
    assert any(
        f.severity == FlagSeverity.YELLOW
        and "alignment_with_item_nature" in f.reason
        for f in flags
    )
    # Range RED should not also fire when alignment is None.
    assert not any(f.reason == "alignment_out_of_range" for f in flags)


def test_item_legacy_v1_alignment_out_of_range_red(world_config):
    """alignment_with_item_nature must be in [-1.0, 1.0]."""
    plugin = get_plugin("item_legacy_v1")
    working = MagicWorking(
        plugin="item_legacy_v1",
        mechanism="discovery",
        actor="Sira Mendes",
        costs={"notice": 0.10},
        domain="physical",
        narrator_basis="x",
        item_id="lassiter",
        alignment_with_item_nature=1.5,  # out of range
    )
    flags = plugin.validate_working(working, world_config)
    assert any(f.severity == FlagSeverity.RED and "alignment" in f.reason for f in flags)


def test_item_legacy_v1_native_mechanism_red_flag(world_config):
    """native delivery is innate_v1's lane; flag RED."""
    plugin = get_plugin("item_legacy_v1")
    working = MagicWorking(
        plugin="item_legacy_v1",
        mechanism="native",
        actor="Sira Mendes",
        costs={"notice": 0.10},
        domain="physical",
        narrator_basis="x",
        item_id="lassiter",
        alignment_with_item_nature=0.5,
    )
    flags = plugin.validate_working(working, world_config)
    assert any(
        f.severity == FlagSeverity.RED and "lane_violation" in f.reason for f in flags
    )


def test_item_legacy_v1_descriptor_loads():
    from sidequest.magic.plugins.item_legacy_v1 import descriptor

    assert descriptor.plugin_id == "item_legacy_v1"
    assert descriptor.source == "item_based"
    assert "discovery" in descriptor.delivery_mechanisms
    assert "mccoy" in descriptor.delivery_mechanisms
    assert descriptor.required_span_attrs == ["item_id", "alignment_with_item_nature"]
