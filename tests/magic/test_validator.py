"""Top-level magic.validator.validate()."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    FlagSeverity,
    HardLimit,
    MagicWorking,
    WorldKnowledge,
    WorldMagicConfig,
)
from sidequest.magic.validator import validate


@pytest.fixture
def world_config() -> WorldMagicConfig:
    return WorldMagicConfig(
        world_slug="coyote_star",
        genre_slug="space_opera",
        allowed_sources=["innate", "item_based"],
        active_plugins=["innate_v1", "item_legacy_v1"],
        intensity=0.25,
        world_knowledge=WorldKnowledge(primary="classified", local_register="folkloric"),
        visibility={"primary": "feared", "local_register": "dismissed"},
        hard_limits=[
            HardLimit(id="no_resurrection", description="death is permanent"),
            HardLimit(id="no_ftl_telepathy", description="psionics bound to local space"),
        ],
        cost_types=["sanity", "notice", "vitality"],
        ledger_bars=[],
        narrator_register="x",
    )


def test_known_but_unregistered_plugin_emits_deep_red_not_keyerror(world_config):
    """Forward-looking entries in `_PLUGIN_SOURCE` (e.g. divine_v1) point to
    plugins that aren't yet implemented in MAGIC_PLUGINS. A misconfigured
    world that activates one must produce a DEEP_RED flag — never an
    unhandled KeyError from the get_plugin call at check #5.
    """
    bad_config = world_config.model_copy(
        update={
            "active_plugins": ["divine_v1"],
            "allowed_sources": ["divine"],
        }
    )
    w = MagicWorking(
        plugin="divine_v1",
        mechanism="cosmic",
        actor="Sira",
        costs={"sanity": 0.1},
        domain="psychic",
        narrator_basis="x",
    )
    flags = validate(w, bad_config)
    assert any(
        f.severity == FlagSeverity.DEEP_RED
        and "plugin_known_but_not_registered" in f.reason
        for f in flags
    )


def test_clean_innate_working(world_config):
    w = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    assert validate(w, world_config) == []


def test_unknown_plugin_deep_red(world_config):
    w = MagicWorking(
        plugin="bargained_for_v1",  # not active in coyote_star
        mechanism="relational",
        actor="Sira",
        costs={"karma": 0.2},
        domain="psychic",
        narrator_basis="x",
    )
    flags = validate(w, world_config)
    assert any(f.severity == FlagSeverity.DEEP_RED and "active_plugins" in f.reason for f in flags)


def test_source_not_allowed_deep_red(world_config):
    """If a plugin's source isn't in allowed_sources, DEEP_RED.

    (Reaching this case requires editing active_plugins manually since
    normally allowed_sources and active_plugins are aligned.)
    """
    bad_config = world_config.model_copy(update={"allowed_sources": ["item_based"]})
    w = MagicWorking(
        plugin="innate_v1",  # innate not in allowed_sources
        mechanism="condition",
        actor="Sira",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    flags = validate(w, bad_config)
    assert any(f.severity == FlagSeverity.DEEP_RED and "allowed_sources" in f.reason for f in flags)


def test_hard_limit_violation_deep_red_via_narrator_basis(world_config):
    """A working whose narrator_basis claims a hard_limit-named effect flags DEEP_RED.

    v1 detects via simple keyword match in narrator_basis (per-limit detector).
    """
    w = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira",
        costs={"sanity": 0.5},
        domain="psychic",
        narrator_basis="resurrection of the dead pilot",
        flavor="acquired",
        consent_state="involuntary",
    )
    flags = validate(w, world_config)
    assert any(f.severity == FlagSeverity.DEEP_RED and "hard_limit" in f.reason for f in flags)


def test_unknown_cost_type_yellow(world_config):
    """Cost-type not in world's cost_types → YELLOW."""
    w = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira",
        costs={"karma": 0.2},  # not in coyote_star cost_types
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    flags = validate(w, world_config)
    assert any(f.severity == FlagSeverity.YELLOW and "cost_type" in f.reason for f in flags)


def test_plugin_validation_flags_propagate(world_config):
    """Plugin-side flags appear in the top-level result."""
    w = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="Sira",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        # missing flavor + consent_state
    )
    flags = validate(w, world_config)
    assert any("flavor" in f.reason for f in flags)
    assert any("consent_state" in f.reason for f in flags)
