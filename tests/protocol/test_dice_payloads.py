"""Tests for dice wire payloads — RollOutcome enum behavior."""
from sidequest.protocol.dice import RollOutcome


def test_roll_outcome_has_tie_member():
    assert RollOutcome.Tie.value == "Tie"


def test_roll_outcome_unknown_wire_value_maps_to_unknown():
    # Existing _missing_ behavior must still hold once Tie is added.
    assert RollOutcome("MysteryTier") is RollOutcome.Unknown
