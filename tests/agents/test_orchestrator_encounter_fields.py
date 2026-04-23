from __future__ import annotations

from sidequest.agents.orchestrator import TurnContext


def test_turn_context_defaults_encounter_summary_none() -> None:
    ctx = TurnContext()
    assert ctx.encounter_summary is None
    assert ctx.confrontation_def is None


def test_turn_context_accepts_encounter_summary_string() -> None:
    ctx = TurnContext(encounter_summary="HP 10/10, beat 0, phase Setup")
    assert ctx.encounter_summary == "HP 10/10, beat 0, phase Setup"


def test_turn_context_accepts_confrontation_def_any() -> None:
    sentinel = object()
    ctx = TurnContext(confrontation_def=sentinel)
    assert ctx.confrontation_def is sentinel
