"""Tests for Opening sub-models (Trigger, Tone, PerPcBeat, SoftHook,
PartyFraming, MagicMicrobleed). OpeningSetting tested in test_opening_setting.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.genre.models.narrative import (
    MagicMicrobleed,
    OpeningTone,
    OpeningTrigger,
    PartyFraming,
    PerPcBeat,
    SoftHook,
)


def test_opening_trigger_defaults() -> None:
    t = OpeningTrigger()
    assert t.mode == "either"
    assert t.min_players == 1
    assert t.max_players == 6
    assert t.backgrounds == []


def test_opening_trigger_solo() -> None:
    t = OpeningTrigger(mode="solo", backgrounds=["Far Landing Raised Me"])
    assert t.mode == "solo"
    assert t.backgrounds == ["Far Landing Raised Me"]


def test_opening_trigger_invalid_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        OpeningTrigger(mode="cooperative")  # not in Literal


def test_opening_tone_defaults() -> None:
    tone = OpeningTone()
    assert tone.register == ""
    assert tone.avoid_at_all_costs == []


def test_per_pc_beat_background_key_accepted() -> None:
    beat = PerPcBeat(applies_to={"background": "Far Landing Raised Me"}, beat="...")
    assert beat.applies_to == {"background": "Far Landing Raised Me"}


def test_per_pc_beat_drive_key_accepted() -> None:
    beat = PerPcBeat(applies_to={"drive": "I Saw Something"}, beat="...")
    assert beat.applies_to == {"drive": "I Saw Something"}


def test_per_pc_beat_unknown_key_rejected() -> None:
    """Validator 6: applies_to keys constrained."""
    with pytest.raises(ValidationError, match="applies_to"):
        PerPcBeat(applies_to={"hometown": "x"}, beat="...")


def test_soft_hook_defaults() -> None:
    h = SoftHook()
    assert h.kind == "pull_not_push"
    assert "conversation lulls" in h.timing


def test_party_framing_already_a_crew() -> None:
    pf = PartyFraming(already_a_crew=True, bond_tier_default="trusted")
    assert pf.already_a_crew is True
    assert pf.bond_tier_default == "trusted"


def test_party_framing_invalid_bond_tier_rejected() -> None:
    with pytest.raises(ValidationError):
        PartyFraming(bond_tier_default="cordial")  # not a BondTier


def test_magic_microbleed_minimal() -> None:
    mb = MagicMicrobleed(detail="The fan ticks at the rhythm of someone humming.")
    assert mb.cost_bar is None


def test_magic_microbleed_with_cost_bar() -> None:
    mb = MagicMicrobleed(detail="...", cost_bar="sanity")
    assert mb.cost_bar == "sanity"
