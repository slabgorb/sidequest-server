from __future__ import annotations

from sidequest.agents.encounter_render import render_encounter_summary
from sidequest.game.encounter import StructuredEncounter


def test_render_combat_summary_lists_metric_phase_actors_beat() -> None:
    enc = StructuredEncounter.combat(combatants=["Rux", "Goblin"], hp=10)
    enc.beat = 2
    out = render_encounter_summary(enc)
    assert "encounter_type: combat" in out
    assert "beat: 2" in out
    assert "phase: Setup" in out
    assert "metric: hp 10/10 (descending, low=0)" in out
    assert "actors:" in out
    assert "- Rux (combatant)" in out
    assert "- Goblin (combatant)" in out


def test_render_respects_ascending_chase_metric() -> None:
    enc = StructuredEncounter.chase(escape_threshold=1.0, rig_type=None, goal=20)
    out = render_encounter_summary(enc)
    assert "metric: separation 0/0 (ascending, high=20)" in out


def test_render_includes_mood_override_when_set() -> None:
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.mood_override = "panic"
    out = render_encounter_summary(enc)
    assert "mood: panic" in out


def test_render_omits_mood_line_when_unset() -> None:
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    out = render_encounter_summary(enc)
    assert "mood:" not in out
