"""MagicState aggregate."""
from __future__ import annotations

import pytest

from sidequest.magic.models import (
    MagicWorking,
)
from sidequest.magic.state import BarKey, MagicState


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


def test_apply_working_unrouted_cost_logs_warning(world_config, caplog):
    """Cost types with no character-scope bar (e.g. world-scope `notice`)
    must surface in the log, never silently disappear. Per CLAUDE.md
    'GM panel is the lie detector' — a skipped subsystem decision that
    leaves no trace is a no-silent-fallback violation. Task 3.5 will
    promote this to an OTEL span; for now a structured log keeps it
    auditable."""
    import logging

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    # `karma` is not a bar in this world's ledger.
    working = MagicWorking(
        plugin="innate_v1",
        mechanism="condition",
        actor="sira_mendes",
        costs={"karma": 0.10},
        domain="psychic",
        narrator_basis="x",
        flavor="acquired",
        consent_state="involuntary",
    )
    with caplog.at_level(logging.WARNING, logger="sidequest.magic.state"):
        state.apply_working(working)

    assert any(
        "magic.unrouted_cost" in r.message and "karma" in r.message for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Wiring tests — GameSnapshot.magic_state (Task 2.3)
# ---------------------------------------------------------------------------


def test_game_snapshot_magic_state_field_defaults_none():
    """GameSnapshot.magic_state must default to None (legacy-save compat)."""
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot()
    assert snap.magic_state is None
    # Verify the field metadata agrees — no model_validator migration (architect Q4).
    field_info = GameSnapshot.model_fields["magic_state"]
    assert field_info.default is None


def test_game_snapshot_magic_state_roundtrips(world_config):
    """GameSnapshot round-trips MagicState through model_dump / model_validate."""
    from sidequest.game.session import GameSnapshot

    state = MagicState.from_config(world_config)
    state.add_character("sira_mendes")

    snap = GameSnapshot(magic_state=state)
    dumped = snap.model_dump()
    restored = GameSnapshot.model_validate(dumped)

    assert restored.magic_state is not None
    sanity_key = BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    assert restored.magic_state.get_bar(sanity_key).value == pytest.approx(1.0)
