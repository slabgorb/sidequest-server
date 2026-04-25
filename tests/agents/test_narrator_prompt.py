from sidequest.agents.narrator import NARRATOR_OUTPUT_ONLY


def test_prompt_documents_npc_side_field():
    # Closed enum surface — narrator must emit `side`.
    assert "side" in NARRATOR_OUTPUT_ONLY
    assert "player" in NARRATOR_OUTPUT_ONLY
    assert "opponent" in NARRATOR_OUTPUT_ONLY
    assert "neutral" in NARRATOR_OUTPUT_ONLY


def test_prompt_documents_beat_outcome_tiers():
    for tier in ("CritFail", "Fail", "Tie", "Success", "CritSuccess"):
        assert tier in NARRATOR_OUTPUT_ONLY


def test_prompt_documents_status_changes_field():
    assert "status_changes" in NARRATOR_OUTPUT_ONLY
    for sev in ("Scratch", "Wound", "Scar"):
        assert sev in NARRATOR_OUTPUT_ONLY


def test_active_encounter_zone_renders_both_dials_and_tags(monkeypatch, build_registry):
    from sidequest.agents.narrator import NarratorAgent
    from sidequest.game.encounter import (
        EncounterActor,
        EncounterMetric,
        StructuredEncounter,
    )
    from sidequest.game.encounter_tag import EncounterTag
    from sidequest.game.status import Status, StatusSeverity
    from sidequest.genre.models.rules import (
        BeatDef,
        ConfrontationDef,
        MetricDef,
    )

    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=4, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=7, starting=0, threshold=10),
        actors=[
            EncounterActor(name="Sam", role="combatant", side="player"),
            EncounterActor(name="Promo", role="combatant", side="opponent"),
        ],
        tags=[EncounterTag(
            text="Off-Balance", created_by="Sam", target="Promo",
            leverage=1, fleeting=False, created_turn=3,
        )],
    )
    cdef = ConfrontationDef(
        type="combat", label="Combat", category="combat",
        player_metric=MetricDef(name="momentum", threshold=10),
        opponent_metric=MetricDef(name="momentum", threshold=10),
        beats=[BeatDef.model_validate({
            "id": "attack", "label": "Attack", "kind": "strike",
            "base": 2, "stat_check": "STR",
        })],
    )
    statuses_by_actor = {"Sam": [Status(
        text="Bruised Ribs", severity=StatusSeverity.Wound,
        absorbed_shifts=0, created_turn=2, created_in_encounter="combat",
    )]}

    registry = build_registry()
    NarratorAgent().build_encounter_context(
        registry, encounter=enc, cdef=cdef,
        statuses_by_actor=statuses_by_actor,
    )

    rendered = registry.render_for("narrator")
    assert "Player metric: 4 / 10" in rendered
    assert "Opponent metric: 7 / 10" in rendered
    assert "Off-Balance" in rendered
    assert "Bruised Ribs" in rendered
    assert "Wound" in rendered
    assert "side=player" in rendered
    assert "side=opponent" in rendered


def test_resolved_encounter_short_circuits_to_resolution_zone(build_registry):
    from sidequest.agents.narrator import NarratorAgent
    from sidequest.game.encounter import (
        EncounterActor,
        EncounterMetric,
        StructuredEncounter,
    )
    from sidequest.game.resolution_signal import ResolutionSignal
    from sidequest.genre.models.rules import (
        BeatDef,
        ConfrontationDef,
        MetricDef,
    )

    enc = StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=4, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=11, starting=0, threshold=10),
        actors=[EncounterActor(name="Sam", role="combatant", side="player")],
        resolved=True,
        outcome="opponent_victory",
    )
    cdef = ConfrontationDef(
        type="combat", label="Combat", category="combat",
        player_metric=MetricDef(name="momentum", threshold=10),
        opponent_metric=MetricDef(name="momentum", threshold=10),
        beats=[BeatDef.model_validate({
            "id": "attack", "label": "Attack", "kind": "strike",
            "base": 2, "stat_check": "STR",
        })],
    )
    signal = ResolutionSignal(
        encounter_type="combat",
        outcome="opponent_victory",
        final_player_metric=4,
        final_opponent_metric=11,
    )

    registry = build_registry()
    NarratorAgent().build_encounter_context(
        registry, encounter=enc, cdef=cdef,
        statuses_by_actor={},
        resolution_signal=signal,
    )

    rendered = registry.render_for("narrator")
    assert "[ENCOUNTER RESOLVED]" in rendered
    assert "outcome: opponent_victory" in rendered
    assert "final_player_metric: 4" in rendered
    assert "final_opponent_metric: 11" in rendered
    # The active-encounter live zone is NOT rendered.
    assert "Available beats" not in rendered
