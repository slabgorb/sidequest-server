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
    for sev in ("Scratch", "Wound", "Scar", "Boon"):
        assert sev in NARRATOR_OUTPUT_ONLY


def test_prompt_documents_boon_for_temporary_buffs():
    """Boon was added 2026-04-30 — give the narrator a slot for prose-described
    magical effects from consumables/scrolls/potions/artifacts (Mira pouch
    playtest gap). Without an explicit rule, the narrator silently dropped
    "the torchlight gets clearer" (a real perception buff) into prose with
    no schema slot.
    """
    # Boon severity is documented and contextualized.
    assert "Boon" in NARRATOR_OUTPUT_ONLY
    # The CRITICAL MAGIC EFFECT RULE wires the Boon-emit obligation to the
    # prose patterns the narrator was previously dropping silently.
    assert "CRITICAL MAGIC EFFECT RULE" in NARRATOR_OUTPUT_ONLY
    # Boon is described as scene-bounded (matches status_clear.py wiring).
    assert (
        "scene end" in NARRATOR_OUTPUT_ONLY.lower()
        or "scene-bounded" in NARRATOR_OUTPUT_ONLY.lower()
    )


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
        tags=[
            EncounterTag(
                text="Off-Balance",
                created_by="Sam",
                target="Promo",
                leverage=1,
                fleeting=False,
                created_turn=3,
            )
        ],
    )
    cdef = ConfrontationDef(
        type="combat",
        label="Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", threshold=10),
        opponent_metric=MetricDef(name="momentum", threshold=10),
        beats=[
            BeatDef.model_validate(
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 2,
                    "stat_check": "STR",
                }
            )
        ],
    )
    statuses_by_actor = {
        "Sam": [
            Status(
                text="Bruised Ribs",
                severity=StatusSeverity.Wound,
                absorbed_shifts=0,
                created_turn=2,
                created_in_encounter="combat",
            )
        ]
    }

    registry = build_registry()
    NarratorAgent().build_encounter_context(
        registry,
        encounter=enc,
        cdef=cdef,
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
        type="combat",
        label="Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", threshold=10),
        opponent_metric=MetricDef(name="momentum", threshold=10),
        beats=[
            BeatDef.model_validate(
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 2,
                    "stat_check": "STR",
                }
            )
        ],
    )
    signal = ResolutionSignal(
        encounter_type="combat",
        outcome="opponent_victory",
        final_player_metric=4,
        final_opponent_metric=11,
    )

    registry = build_registry()
    NarratorAgent().build_encounter_context(
        registry,
        encounter=enc,
        cdef=cdef,
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


def test_resolved_encounter_yielded_branch_renders_actors_and_edge(build_registry):
    """Yielded-branch prose includes yielded_actors and edge_refreshed.

    Phase 2 reviewer flagged this as deferred coverage; now asserted here.
    """
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
        opponent_metric=EncounterMetric(name="momentum", current=7, starting=0, threshold=10),
        actors=[EncounterActor(name="Sam", role="combatant", side="player")],
        resolved=True,
        outcome="yielded",
    )
    cdef = ConfrontationDef(
        type="combat",
        label="Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", threshold=10),
        opponent_metric=MetricDef(name="momentum", threshold=10),
        beats=[
            BeatDef.model_validate(
                {
                    "id": "attack",
                    "label": "Attack",
                    "kind": "strike",
                    "base": 2,
                    "stat_check": "STR",
                }
            )
        ],
    )
    signal = ResolutionSignal(
        encounter_type="combat",
        outcome="yielded",
        final_player_metric=4,
        final_opponent_metric=7,
        yielded_actors=("Sam",),
        edge_refreshed=3,
    )

    registry = build_registry()
    NarratorAgent().build_encounter_context(
        registry,
        encounter=enc,
        cdef=cdef,
        statuses_by_actor={},
        resolution_signal=signal,
    )

    rendered = registry.render_for("narrator")
    assert "[ENCOUNTER RESOLVED]" in rendered
    assert "outcome: yielded" in rendered
    assert "yielded_actors: [Sam]" in rendered
    assert "edge_refreshed: 3" in rendered
    # The active-encounter live zone is NOT rendered for a resolved encounter.
    assert "Available beats" not in rendered
