"""Tests for turn management — TurnManager and PreprocessedAction."""

from sidequest.game.turn import PreprocessedAction


def test_preprocessed_action_has_no_flag_fields():
    """Group A Task 4 — five flag booleans retired from PreprocessedAction."""
    field_names = set(PreprocessedAction.model_fields.keys())
    dead = [
        "is_power_grab",
        "references_inventory",
        "references_npc",
        "references_ability",
        "references_location",
    ]
    present = [d for d in dead if d in field_names]
    assert not present, (
        f"Dead flag fields still on PreprocessedAction: {present}"
    )


def test_preprocessed_action_keeps_perspective_fields():
    """Guard: you/named/intent stay — scaffolding for Group B decomposer."""
    field_names = set(PreprocessedAction.model_fields.keys())
    for live in ["you", "named", "intent"]:
        assert live in field_names, f"{live} must remain on PreprocessedAction"
