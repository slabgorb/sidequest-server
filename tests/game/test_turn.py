"""Tests for turn management — TurnManager and PreprocessedAction."""

from sidequest.game.turn import PreprocessedAction, TurnManager, TurnPhase


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


# ---------------------------------------------------------------------------
# Story 45-11 — record_interaction must keep ``round`` in lockstep with
# ``interaction``. Felix's Playtest 3 save ended round=65/interaction=72
# because record_interaction only advanced ``interaction`` and the
# never-called ``advance_round`` left the display counter frozen.
# ---------------------------------------------------------------------------


def test_record_interaction_advances_round_in_lockstep() -> None:
    """``record_interaction`` must advance BOTH counters.

    The narrative_log writer feeds ``round_number = turn_manager.interaction``
    today (session_handler write site). For ``turn_manager.round`` to keep
    pace with ``MAX(narrative_log.round_number)``, every interaction
    advance must carry the round counter with it. Anything else reproduces
    the Felix divergence (round=65, max=72).

    RED until 45-11 GREEN wires advance_round into record_interaction
    (Strategy A from context-story-45-11.md §"Decide between two
    structural fixes").
    """
    tm = TurnManager(round=1, interaction=1)
    tm.record_interaction()
    assert tm.interaction == 2, "interaction must still advance by 1"
    assert tm.round == 2, (
        f"round must advance in lockstep with interaction; got round={tm.round}, "
        f"interaction={tm.interaction}. This is the Felix divergence — fix in 45-11."
    )

    # Drive a few more interactions and confirm the lockstep holds.
    for expected in range(3, 8):
        tm.record_interaction()
        assert tm.interaction == expected
        assert tm.round == expected, (
            f"after {expected - 1} interactions, "
            f"round={tm.round} != interaction={tm.interaction}"
        )


def test_record_interaction_preserves_phase_reset() -> None:
    """AC6 regression — phase still resets to InputCollection.

    Whatever wiring 45-11 lands for ``round`` advance must NOT regress the
    existing phase-reset semantics that downstream code relies on.
    """
    tm = TurnManager(
        round=5, interaction=5, phase=TurnPhase.AgentExecution,
    )
    tm.record_interaction()
    assert tm.phase == TurnPhase.InputCollection


def test_record_interaction_preserves_submitted_clear() -> None:
    """AC6 regression — submitted set still clears on interaction record."""
    tm = TurnManager(round=1, interaction=1, player_count=2)
    tm.submit_input("p1")
    submitted = object.__getattribute__(tm, "_submitted")
    assert submitted == {"p1"}

    tm.record_interaction()
    submitted = object.__getattribute__(tm, "_submitted")
    assert submitted == set(), (
        f"submitted set must clear after record_interaction, got {submitted}"
    )
