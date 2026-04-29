"""Voice resolver returns name-form for current bond tier."""
from __future__ import annotations

import pytest

from sidequest.agents.subsystems.chassis_voice import resolve_chassis_name_form
from sidequest.game.chassis import (
    BondLedgerEntry,
    ChassisInstance,
    apply_bond_event,
)
from sidequest.genre.models.chassis import ChassisVoiceSpec


def _kestrel(strength: float = 0.45) -> ChassisInstance:
    return ChassisInstance(
        id="kestrel",
        name="Kestrel",
        class_id="voidborn_freighter",
        voice=ChassisVoiceSpec(
            default_register="dry_warm",
            vocal_tics=["theatrical sigh"],
            silence_register="approving_or_sulking_context_dependent",
            name_forms_by_bond_tier={
                "severed": "Pilot",
                "hostile": "Pilot",
                "strained": "Pilot",
                "neutral": "Pilot",
                "familiar": "Mr. {last_name}",
                "trusted": "{first_name}",
                "fused": "{nickname}",
            },
        ),
        bond_ledger=[
            BondLedgerEntry(
                character_id="zee",
                bond_strength_character_to_chassis=strength,
                bond_strength_chassis_to_character=strength,
                bond_tier_character="trusted",
                bond_tier_chassis="trusted",
            ),
        ],
    )


class _FakeCharacter:
    """Mirrors the shape resolve_chassis_name_form expects."""
    def __init__(
        self,
        *,
        id: str,
        first_name: str,
        last_name: str,
        nickname: str | None = None,
    ) -> None:
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.nickname = nickname


def test_resolves_first_name_at_trusted_tier() -> None:
    chassis = _kestrel(0.45)
    zee = _FakeCharacter(id="zee", first_name="Zee", last_name="Jones")
    assert resolve_chassis_name_form(chassis, zee) == "Zee"


def test_resolves_last_name_form_after_drop_to_familiar() -> None:
    chassis = _kestrel(0.45)
    apply_bond_event(
        chassis=chassis,
        character_id="zee",
        delta_character=-0.10,
        delta_chassis=-0.10,
        reason="contrived",
        confrontation_id=None,
        turn_id=1,
    )
    zee = _FakeCharacter(id="zee", first_name="Zee", last_name="Jones")
    assert resolve_chassis_name_form(chassis, zee) == "Mr. Jones"


def test_no_voice_returns_default_pilot() -> None:
    chassis = _kestrel(0.45)
    chassis.voice = None
    zee = _FakeCharacter(id="zee", first_name="Zee", last_name="Jones")
    assert resolve_chassis_name_form(chassis, zee) == "Pilot"


def test_no_bond_entry_returns_default_pilot() -> None:
    chassis = _kestrel(0.45)
    stranger = _FakeCharacter(id="not_in_ledger", first_name="A", last_name="B")
    assert resolve_chassis_name_form(chassis, stranger) == "Pilot"


def test_missing_nickname_at_fused_falls_back_to_first_name() -> None:
    chassis = _kestrel(0.85)
    chassis.bond_ledger[0].bond_tier_chassis = "fused"
    zee = _FakeCharacter(id="zee", first_name="Zee", last_name="Jones", nickname=None)
    # Fallback per spec §7 — fused with no nickname source falls back to {first_name}.
    assert resolve_chassis_name_form(chassis, zee) == "Zee"


def test_renders_nickname_when_present() -> None:
    chassis = _kestrel(0.85)
    chassis.bond_ledger[0].bond_tier_chassis = "fused"
    zee = _FakeCharacter(
        id="zee", first_name="Zee", last_name="Jones", nickname="Captain Velocity",
    )
    assert resolve_chassis_name_form(chassis, zee) == "Captain Velocity"


@pytest.mark.parametrize("tier,expected", [
    ("severed", "Pilot"),
    ("hostile", "Pilot"),
    ("strained", "Pilot"),
    ("neutral", "Pilot"),
])
def test_low_tiers_return_pilot(tier: str, expected: str) -> None:
    chassis = _kestrel(0.45)
    chassis.bond_ledger[0].bond_tier_chassis = tier  # type: ignore[assignment]
    zee = _FakeCharacter(id="zee", first_name="Zee", last_name="Jones")
    assert resolve_chassis_name_form(chassis, zee) == expected
