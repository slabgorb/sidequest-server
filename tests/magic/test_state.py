"""MagicState aggregate."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    MagicWorking,
    WorldKnowledge,
    WorldMagicConfig,
)
from sidequest.magic.state import BarKey, MagicState


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


def test_initialize_for_character(world_config):
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    sanity_key = BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    bar = state.get_bar(sanity_key)
    assert bar.value == 1.0  # starts_at_chargen
    notice_key = BarKey(scope="character", owner_id="sira_mendes", bar_id="notice")
    assert state.get_bar(notice_key).value == 0.0


def test_world_bar_initialized_at_world_load(world_config):
    state = MagicState.from_config(world_config)

    heat_key = BarKey(scope="world", owner_id="coyote_reach", bar_id="hegemony_heat")
    assert state.get_bar(heat_key).value == 0.30


def test_apply_working_debits_costs(world_config):
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="sira_mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    result = state.apply_working(working)

    assert result.crossings == []
    assert state.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    ).value == pytest.approx(0.88)


def test_apply_working_records_in_log(world_config):
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="sira_mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    state.apply_working(working)

    assert len(state.working_log) == 1
    assert state.working_log[0].plugin == "innate_v1"


def test_threshold_crossing_returns_in_apply_result(world_config):
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    # Pre-set sanity to 0.45 then apply working with cost 0.10 → crosses 0.40
    sanity_key = BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    state.set_bar_value(sanity_key, 0.45)

    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="sira_mendes",
        costs={"sanity": 0.10},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    result = state.apply_working(working)

    assert len(result.crossings) == 1
    assert result.crossings[0].bar_key.bar_id == "sanity"
    assert "Bleeding-Through" in result.crossings[0].consequence


def test_apply_working_unknown_actor_raises(world_config):
    state = MagicState.from_config(world_config)
    # No character added

    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="unknown",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
    )
    with pytest.raises(KeyError, match="unknown"):
        state.apply_working(working)


def test_pydantic_serialization_roundtrip(world_config):
    """MagicState serializes to/from dict (for SQLite save)."""
    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="sira_mendes",
        costs={"sanity": 0.12},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    state.apply_working(working)

    dumped = state.model_dump()
    restored = MagicState.model_validate(dumped)
    assert (
        restored.get_bar(
            BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
        ).value
        == pytest.approx(0.88)
    )
    assert len(restored.working_log) == 1
