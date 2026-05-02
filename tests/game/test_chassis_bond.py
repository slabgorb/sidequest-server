"""Bond mutation, tier derivation, lineage append."""

from __future__ import annotations

from sidequest.game.chassis import (
    BondLedgerEntry,
    ChassisInstance,
    apply_bond_event,
    apply_chassis_lineage_intimate,
    derive_bond_tier,
)


def test_derive_bond_tier_thresholds() -> None:
    assert derive_bond_tier(-0.95) == "severed"
    assert derive_bond_tier(-0.6) == "hostile"
    assert derive_bond_tier(-0.2) == "strained"
    assert derive_bond_tier(0.0) == "neutral"
    assert derive_bond_tier(0.2) == "familiar"
    assert derive_bond_tier(0.5) == "trusted"
    assert derive_bond_tier(0.9) == "fused"


def _kestrel_with_player_bond() -> ChassisInstance:
    return ChassisInstance(
        id="kestrel",
        name="Kestrel",
        class_id="voidborn_freighter",
        bond_ledger=[
            BondLedgerEntry(
                character_id="player_character_1",
                bond_strength_character_to_chassis=0.45,
                bond_strength_chassis_to_character=0.45,
                bond_tier_character="trusted",
                bond_tier_chassis="trusted",
                history=[],
            ),
        ],
    )


def test_apply_bond_event_updates_strength_and_tier() -> None:
    chassis = _kestrel_with_player_bond()
    result = apply_bond_event(
        chassis=chassis,
        character_id="player_character_1",
        delta_character=0.04,
        delta_chassis=0.06,
        reason="the_tea_brew clear_win",
        confrontation_id="the_tea_brew",
        turn_id=12,
    )
    entry = chassis.bond_ledger[0]
    assert entry.bond_strength_chassis_to_character == 0.51
    assert entry.bond_strength_character_to_chassis == 0.49
    assert entry.bond_tier_chassis == "trusted"
    assert entry.bond_tier_character == "trusted"
    assert len(entry.history) == 1
    assert entry.history[0].reason == "the_tea_brew clear_win"
    assert result.tier_chassis_crossed is False


def test_apply_bond_event_detects_tier_crossing() -> None:
    chassis = _kestrel_with_player_bond()
    chassis.bond_ledger[0].bond_strength_chassis_to_character = 0.83
    chassis.bond_ledger[0].bond_strength_character_to_chassis = 0.83
    result = apply_bond_event(
        chassis=chassis,
        character_id="player_character_1",
        delta_character=0.05,
        delta_chassis=0.05,
        reason="threshold cross test",
        confrontation_id="the_tea_brew",
        turn_id=20,
    )
    entry = chassis.bond_ledger[0]
    assert entry.bond_tier_chassis == "fused"
    assert entry.bond_tier_character == "fused"
    assert result.tier_chassis_crossed is True
    assert result.tier_character_crossed is True


def test_apply_bond_event_clamps_to_unit_range() -> None:
    chassis = _kestrel_with_player_bond()
    chassis.bond_ledger[0].bond_strength_chassis_to_character = 0.97
    apply_bond_event(
        chassis=chassis,
        character_id="player_character_1",
        delta_character=0.0,
        delta_chassis=0.50,  # would go to 1.47 without clamp
        reason="overflow guard",
        confrontation_id=None,
        turn_id=30,
    )
    assert chassis.bond_ledger[0].bond_strength_chassis_to_character == 1.0


def test_apply_bond_event_missing_entry_raises() -> None:
    chassis = _kestrel_with_player_bond()
    import pytest

    with pytest.raises(ValueError, match="no bond ledger entry"):
        apply_bond_event(
            chassis=chassis,
            character_id="not_a_character",
            delta_character=0.04,
            delta_chassis=0.06,
            reason="should fail",
            confrontation_id=None,
            turn_id=1,
        )


def test_apply_chassis_lineage_intimate_appends() -> None:
    chassis = _kestrel_with_player_bond()
    apply_chassis_lineage_intimate(
        chassis=chassis,
        narrative_seed="the captain's tea cup left for the ghost of the previous captain",
        turn_id=12,
        confrontation_id="the_tea_brew",
    )
    assert len(chassis.lineage) == 1
    assert chassis.lineage[0].kind == "intimate"
    assert chassis.lineage[0].turn_id == 12
