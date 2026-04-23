"""Tests for ``sidequest.game.scenario_state`` — Story 2.3 Slice D.

Exercises :meth:`ScenarioState.from_genre_pack` role assignment,
guilty-NPC selection (both can_be_guilty and fallback paths),
adjacency graph shape, and JSON round-trip.
"""

from __future__ import annotations

import random

from sidequest.game.scenario_state import ScenarioRole, ScenarioState
from sidequest.genre.models.scenario import (
    AssignmentMatrix,
    InitialBeliefs,
    Pacing,
    ScenarioNpc,
    ScenarioPack,
    Suspect,
    Suspicion,
    WhenGuilty,
    WhenInnocent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _npc(
    npc_id: str,
    name: str,
    *,
    suspicions: list[Suspicion] | None = None,
    facts: list[str] | None = None,
) -> ScenarioNpc:
    return ScenarioNpc(
        id=npc_id,
        archetype_ref="witness",
        name=name,
        initial_beliefs=InitialBeliefs(
            facts=facts or [],
            suspicions=suspicions or [],
        ),
        when_guilty=WhenGuilty(truth="", cover_story="", breaking_evidence=[]),
        when_innocent=WhenInnocent(actual_activity=""),
    )


def _pack(
    *,
    npcs: list[ScenarioNpc],
    suspects: list[Suspect],
) -> ScenarioPack:
    return ScenarioPack(
        name="Test Scenario",
        version="1.0",
        description="",
        duration_minutes=90,
        max_players=3,
        pacing=Pacing(scene_budget=5),
        assignment_matrix=AssignmentMatrix(suspects=suspects),
        npcs=npcs,
    )


# ---------------------------------------------------------------------------
# Guilty selection
# ---------------------------------------------------------------------------


class TestGuiltySelection:
    def test_picks_only_from_can_be_guilty_suspects(self) -> None:
        pack = _pack(
            npcs=[_npc("a", "Ada"), _npc("b", "Bert"), _npc("c", "Cleo")],
            suspects=[
                Suspect(id="a", archetype_ref="r", can_be_guilty=False),
                Suspect(id="b", archetype_ref="r", can_be_guilty=True),
                Suspect(id="c", archetype_ref="r", can_be_guilty=True),
            ],
        )
        # Use a seeded RNG over many samples to confirm only b and c ever appear.
        outcomes = set()
        for seed in range(32):
            state = ScenarioState.from_genre_pack(pack, rng=random.Random(seed))
            outcomes.add(state.guilty_npc)
        assert outcomes <= {"b", "c"}
        # With 32 seeds, at least one of each should land.
        assert "b" in outcomes and "c" in outcomes

    def test_fallback_to_first_npc_when_no_eligible_suspect(self) -> None:
        pack = _pack(
            npcs=[_npc("only", "Ada")],
            suspects=[Suspect(id="only", archetype_ref="r", can_be_guilty=False)],
        )
        state = ScenarioState.from_genre_pack(pack)
        assert state.guilty_npc == "only"

    def test_empty_pack_produces_empty_guilty(self) -> None:
        pack = _pack(npcs=[], suspects=[])
        state = ScenarioState.from_genre_pack(pack)
        assert state.guilty_npc == ""
        assert state.npc_roles == {}
        assert state.adjacency == {}


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------


class TestRoleAssignment:
    def test_guilty_npc_name_gets_guilty_role(self) -> None:
        pack = _pack(
            npcs=[
                _npc("a", "Ada"),
                _npc("b", "Bert", suspicions=[Suspicion(target="Ada", confidence=0.6, basis="x")]),
            ],
            suspects=[Suspect(id="a", archetype_ref="r", can_be_guilty=True)],
        )
        state = ScenarioState.from_genre_pack(pack)
        assert state.guilty_npc == "a"
        # npc_roles is keyed by NAME, not id (Rust parity).
        assert state.npc_roles["Ada"] == ScenarioRole.Guilty
        assert state.npc_roles["Bert"] == ScenarioRole.Witness

    def test_no_initial_suspicions_means_innocent(self) -> None:
        pack = _pack(
            npcs=[_npc("a", "Ada"), _npc("b", "Bert")],
            suspects=[Suspect(id="a", archetype_ref="r", can_be_guilty=True)],
        )
        state = ScenarioState.from_genre_pack(pack)
        assert state.npc_roles["Bert"] == ScenarioRole.Innocent


# ---------------------------------------------------------------------------
# Adjacency graph
# ---------------------------------------------------------------------------


class TestAdjacency:
    def test_fully_connected_excludes_self(self) -> None:
        pack = _pack(
            npcs=[_npc("a", "Ada"), _npc("b", "Bert"), _npc("c", "Cleo")],
            suspects=[Suspect(id="a", archetype_ref="r", can_be_guilty=True)],
        )
        state = ScenarioState.from_genre_pack(pack)
        assert set(state.adjacency.keys()) == {"Ada", "Bert", "Cleo"}
        assert set(state.adjacency["Ada"]) == {"Bert", "Cleo"}
        assert set(state.adjacency["Bert"]) == {"Ada", "Cleo"}
        assert "Ada" not in state.adjacency["Ada"]


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


class TestMutationHelpers:
    def test_set_tension_clamps(self) -> None:
        state = ScenarioState()
        state.set_tension(1.5)
        assert state.tension == 1.0
        state.set_tension(-0.2)
        assert state.tension == 0.0

    def test_discover_and_question_are_idempotent(self) -> None:
        state = ScenarioState()
        state.discover_clue("c1")
        state.discover_clue("c1")
        state.record_questioned_npc("Ada")
        state.record_questioned_npc("Ada")
        assert state.discovered_clues == {"c1"}
        assert state.questioned_npcs == {"Ada"}


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_scenario_state_json_round_trip(self) -> None:
        pack = _pack(
            npcs=[_npc("a", "Ada"), _npc("b", "Bert")],
            suspects=[Suspect(id="a", archetype_ref="r", can_be_guilty=True)],
        )
        state = ScenarioState.from_genre_pack(pack, rng=random.Random(0))
        state.set_tension(0.4)
        state.discover_clue("clue_alpha")
        state.record_questioned_npc("Bert")

        restored = ScenarioState.model_validate_json(state.model_dump_json())
        assert restored.guilty_npc == state.guilty_npc
        assert restored.npc_roles == state.npc_roles
        assert restored.adjacency == state.adjacency
        assert restored.tension == 0.4
        assert restored.discovered_clues == {"clue_alpha"}
        assert restored.questioned_npcs == {"Bert"}
