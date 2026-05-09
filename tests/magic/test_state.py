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

    heat_key = BarKey(scope="world", owner_id="coyote_star", bar_id="hegemony_heat")
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
    assert restored.get_bar(
        BarKey(scope="character", owner_id="sira_mendes", bar_id="sanity")
    ).value == pytest.approx(0.88)
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

    assert any("magic.unrouted_cost" in r.message and "karma" in r.message for r in caplog.records)


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


# --- Class-aware spell-slot allocation (B/X pivot 2026-05-07) ---------------


def _class_keyed_world_config() -> WorldMagicConfig:
    """Synthetic config exercising the class-keyed starts_at_chargen path.

    Mirrors the caverns_sunden shape but stays self-contained so this
    test doesn't depend on shipped content YAML.
    """
    return WorldMagicConfig(
        world_slug="bx_test_world",
        genre_slug="bx_test_genre",
        allowed_sources=["innate"],
        active_plugins=["innate_v1"],
        intensity=0.5,
        world_knowledge=WorldKnowledge(primary="folkloric"),
        visibility={"primary": "feared"},
        hard_limits=[HardLimit(id="no_test", description="ban resurrection")],
        cost_types=["spell_slots"],
        ledger_bars=[
            LedgerBarSpec(
                id="spell_slots",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                threshold_low=0.0,
                starts_at_chargen={
                    "Mage": 1.0,
                    "Cleric": 0.0,
                    "Fighter": 0.0,
                },
            ),
        ],
        narrator_register="test register",
    )


def test_add_character_class_aware_resolution_mage_gets_slot():
    state = MagicState.from_config(_class_keyed_world_config())
    state.add_character("Gandalf", character_class="Mage")
    bar = state.get_bar(BarKey(scope="character", owner_id="Gandalf", bar_id="spell_slots"))
    assert bar.value == 1.0


def test_add_character_class_aware_resolution_cleric_gets_zero():
    state = MagicState.from_config(_class_keyed_world_config())
    state.add_character("Sister_Anya", character_class="Cleric")
    bar = state.get_bar(BarKey(scope="character", owner_id="Sister_Anya", bar_id="spell_slots"))
    assert bar.value == 0.0


def test_add_character_missing_class_param_with_dict_spec_raises():
    state = MagicState.from_config(_class_keyed_world_config())
    with pytest.raises(ValueError, match=r"no character_class was supplied"):
        state.add_character("Mira")  # no character_class


def test_add_character_unknown_class_raises_with_keys_listed():
    state = MagicState.from_config(_class_keyed_world_config())
    with pytest.raises(ValueError, match=r"missing from starts_at_chargen") as exc:
        state.add_character("Mira", character_class="Bard")
    # Error must list available keys so the authoring fix is obvious.
    msg = str(exc.value)
    assert "Mage" in msg
    assert "Cleric" in msg
    assert "Bard" in msg


def test_add_character_scalar_spec_ignores_class_param(world_config):
    """Coyote-Star world has scalar starts_at_chargen on every bar.
    Passing or omitting ``character_class`` must produce the same
    initial values — the class param is opt-in per spec shape.
    """
    state_a = MagicState.from_config(world_config)
    state_a.add_character("alice")
    state_b = MagicState.from_config(world_config)
    state_b.add_character("bob", character_class="Mage")

    sanity_a = state_a.get_bar(BarKey(scope="character", owner_id="alice", bar_id="sanity"))
    sanity_b = state_b.get_bar(BarKey(scope="character", owner_id="bob", bar_id="sanity"))
    assert sanity_a.value == sanity_b.value


def test_add_character_idempotent_with_class():
    """Re-calling ``add_character`` for the same id is idempotent (the
    MP same-slug second-commit path) — even when class-keyed bars are
    present, the second call must not duplicate or re-init the bar.
    """
    state = MagicState.from_config(_class_keyed_world_config())
    state.add_character("Gandalf", character_class="Mage")
    bar_key = BarKey(scope="character", owner_id="Gandalf", bar_id="spell_slots")
    state.set_bar_value(bar_key, 0.5)  # simulate spending a slot mid-session
    state.add_character("Gandalf", character_class="Mage")
    # Idempotent: the bar's mid-session value is preserved, not reset.
    assert state.get_bar(bar_key).value == 0.5
