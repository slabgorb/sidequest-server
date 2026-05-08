"""learned_v1 plugin behavior."""

from __future__ import annotations

from sidequest.magic.models import WorldKnowledge, WorldMagicConfig


def _world_cfg(**overrides) -> WorldMagicConfig:
    """Build a minimal WorldMagicConfig for these tests.

    The plan's test bodies construct WorldMagicConfig with six kwargs; the
    model now requires several more (genre_slug, intensity, visibility,
    cost_types, narrator_register). This helper supplies minimal stand-ins
    so the assertions below remain verbatim from the plan.
    """
    base: dict = dict(
        world_slug="test",
        genre_slug="caverns_and_claudes",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        intensity=0.5,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "feared"},
        ledger_bars=[],
        hard_limits=[],
        cost_types=["slots_l1"],
        narrator_register="test",
    )
    base.update(overrides)
    return WorldMagicConfig(**base)


def test_learned_v1_registered_in_magic_plugins():
    from sidequest.magic.plugin import MAGIC_PLUGINS

    assert "learned_v1" in MAGIC_PLUGINS
    assert MAGIC_PLUGINS["learned_v1"].plugin_id == "learned_v1"


def test_learned_v1_validate_flags_missing_spell_id():
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.plugin import MAGIC_PLUGINS

    plugin = MAGIC_PLUGINS["learned_v1"]
    cfg = _world_cfg(
        world_slug="test",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        world_knowledge=WorldKnowledge(primary="folkloric"),
        ledger_bars=[],
        hard_limits=[],
    )
    w = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="missing spell_id",
        # spell_id intentionally omitted
        slot_level=1,
        costs={"slots_l1": 1.0},
    )

    flags = plugin.validate_working(w, cfg)
    assert any(f.reason == "missing_required_attr_spell_id" for f in flags)


def test_learned_v1_validate_clean_when_complete():
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.plugin import MAGIC_PLUGINS

    plugin = MAGIC_PLUGINS["learned_v1"]
    cfg = _world_cfg(
        world_slug="test",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        world_knowledge=WorldKnowledge(primary="folkloric"),
        ledger_bars=[],
        hard_limits=[],
    )
    w = MagicWorking(
        plugin="learned_v1",
        mechanism="studied",
        actor="rux",
        domain="physical",
        narrator_basis="ok",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )

    flags = plugin.validate_working(w, cfg)
    assert flags == []


def test_learned_v1_validate_rejects_item_lane():
    """learned_v1 firing with discovery is item_legacy_v1 territory — lane violation."""
    from sidequest.magic.models import MagicWorking
    from sidequest.magic.plugin import MAGIC_PLUGINS

    plugin = MAGIC_PLUGINS["learned_v1"]
    cfg = _world_cfg(
        world_slug="test",
        allowed_sources=["learned"],
        active_plugins=["learned_v1"],
        world_knowledge=WorldKnowledge(primary="folkloric"),
        ledger_bars=[],
        hard_limits=[],
    )
    w = MagicWorking(
        plugin="learned_v1",
        mechanism="discovery",  # wrong lane
        actor="rux",
        domain="physical",
        narrator_basis="bad mechanism",
        spell_id="magic_missile",
        slot_level=1,
        costs={"slots_l1": 1.0},
    )

    flags = plugin.validate_working(w, cfg)
    assert any(f.reason == "learned_via_item_mechanism_is_lane_violation" for f in flags)
