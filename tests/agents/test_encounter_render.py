from __future__ import annotations

from sidequest.agents.encounter_render import render_encounter_summary
from sidequest.game.encounter import EncounterActor, EncounterMetric, StructuredEncounter


def _make_combat(
    *,
    actors: list[str] | None = None,
    player_current: int = 0,
    threshold: int = 10,
) -> StructuredEncounter:
    actor_names = actors or ["Rux"]
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(
            name="momentum", current=player_current, starting=0, threshold=threshold,
        ),
        opponent_metric=EncounterMetric(
            name="momentum", current=0, starting=0, threshold=threshold,
        ),
        actors=[
            EncounterActor(name=n, role="combatant", side="player")
            for n in actor_names
        ],
    )


def test_render_combat_summary_lists_metric_phase_beat() -> None:
    enc = _make_combat()
    enc.beat = 2
    out = render_encounter_summary(enc)
    assert "[ENCOUNTER: combat]" in out
    assert "Beat: 2" in out
    assert "Phase: Setup" in out
    assert "momentum" in out  # metric name present in player/opponent lines
    assert "10" in out  # threshold


def test_render_respects_ascending_player_metric() -> None:
    enc = _make_combat(player_current=5, threshold=20)
    out = render_encounter_summary(enc)
    assert "5" in out   # current value rendered
    assert "20" in out  # threshold rendered


def test_render_includes_mood_override_when_set() -> None:
    enc = _make_combat()
    enc.mood_override = "panic"
    out = render_encounter_summary(enc)
    assert "panic" in out


def test_render_omits_mood_line_when_unset() -> None:
    enc = _make_combat()
    out = render_encounter_summary(enc)
    assert "mood" not in out.lower()
