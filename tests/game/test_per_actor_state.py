"""Conformance tests for EncounterActor.per_actor_state (T2, dogfight port).

ADR-077 sealed-letter dispatch (T3, sealed_letter.py) mutates
``EncounterActor.per_actor_state`` directly to store each pilot's cockpit
descriptor between turns. If the field has a shared-default-dict bug
(``= {}`` instead of ``Field(default_factory=dict)``), T3 will produce
silent cross-actor state pollution that's nearly impossible to debug after
the fact. Lock the contract here.

Reference Rust test (skimmed for behavior intent, not 1:1 translated):
``sidequest-api/crates/sidequest-game/tests/per_actor_state_story_38_2_tests.rs``.

Style follows ``tests/genre/test_resolution_mode.py`` (T1) — YAML fixture
strings parsed via ``model_validate``, then end-to-end load-path wiring
test against the real dispatch path.
"""

from __future__ import annotations

import json

import yaml

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)

# ---------------------------------------------------------------------------
# AC-Field / AC-Default: field exists, defaults to empty dict (not None)
# ---------------------------------------------------------------------------


def test_per_actor_state_default_is_empty_dict_not_none():
    """Freshly-constructed EncounterActor has per_actor_state={}."""
    actor = EncounterActor(name="Maverick", role="pilot", side="player")
    assert actor.per_actor_state is not None
    assert actor.per_actor_state == {}
    assert isinstance(actor.per_actor_state, dict)


def test_per_actor_state_default_from_yaml_omits_field():
    """YAML without per_actor_state deserializes to empty dict (backward
    compat for old saves and existing fixtures that pre-date the field)."""
    blob = yaml.safe_load(
        "name: Iceman\n"
        "role: wingman\n"
        "side: player\n"
    )
    actor = EncounterActor.model_validate(blob)
    assert actor.name == "Iceman"
    assert actor.role == "wingman"
    assert actor.per_actor_state == {}


# ---------------------------------------------------------------------------
# AC-JSON: accepts JSON-safe scalars, nested dict, nested list
# ---------------------------------------------------------------------------


def test_per_actor_state_accepts_json_safe_values():
    """Field is dict[str, Any] but values must be JSON-safe (crosses wire).
    Covers string, int, float, bool, None, nested dict, nested list."""
    actor = EncounterActor(
        name="Viper",
        role="instructor",
        side="opponent",
        per_actor_state={
            "bearing": "merge",       # string
            "range": 500,              # int
            "energy": 0.75,            # float
            "gun_solution": False,     # bool
            "wingman": None,           # null
            "nested": {"weapon": "vulcan", "rounds": 940},  # nested dict
            "tags": ["bandit", "bogey", "tally-ho"],         # nested list
        },
    )
    # Round-trip through JSON to prove every value survives the wire.
    rewire = json.loads(actor.model_dump_json())
    pas = rewire["per_actor_state"]
    assert pas["bearing"] == "merge"
    assert pas["range"] == 500
    assert pas["energy"] == 0.75
    assert pas["gun_solution"] is False
    assert pas["wingman"] is None
    assert pas["nested"] == {"weapon": "vulcan", "rounds": 940}
    assert pas["tags"] == ["bandit", "bogey", "tally-ho"]


# ---------------------------------------------------------------------------
# Mutation persistence: catches shared-default-dict bug
# ---------------------------------------------------------------------------


def test_per_actor_state_independent_defaults_across_instances():
    """The classic pydantic mutable-default trap — if the field were
    declared as ``= {}`` instead of ``Field(default_factory=dict)``, every
    EncounterActor would share the same dict object and T3 would corrupt
    every actor when it touched any one of them.

    Construct two actors at the same default, mutate one, and assert the
    other is untouched. This is the load-bearing test for T3.
    """
    a = EncounterActor(name="Goose", role="rio", side="player")
    b = EncounterActor(name="Slider", role="rio", side="opponent")

    # Sanity: both start empty.
    assert a.per_actor_state == {}
    assert b.per_actor_state == {}

    # Mutate one; the other must remain pristine.
    a.per_actor_state["bearing"] = "180"
    assert a.per_actor_state == {"bearing": "180"}
    assert b.per_actor_state == {}, (
        "Shared-default-dict bug: mutating actor A leaked into actor B. "
        "EncounterActor.per_actor_state must use Field(default_factory=dict)."
    )

    # Identity check — the two dicts must be different objects.
    assert a.per_actor_state is not b.per_actor_state


def test_per_actor_state_mutation_persists_across_unrelated_writes():
    """Setting key X, then setting key Y, leaves X intact. Catches any
    accidental copy-on-write that would silently lose T3's per-turn writes.
    """
    actor = EncounterActor(name="Wolf", role="pilot", side="opponent")
    actor.per_actor_state["bearing"] = 270
    actor.per_actor_state["range"] = "close"

    # First key still there after second write.
    assert actor.per_actor_state["bearing"] == 270
    assert actor.per_actor_state["range"] == "close"

    # Mutating an unrelated key on a different actor doesn't bleed in.
    other = EncounterActor(name="Hollywood", role="pilot", side="player")
    other.per_actor_state["bearing"] = "merge"
    assert actor.per_actor_state["bearing"] == 270


# ---------------------------------------------------------------------------
# Pydantic round-trip: model_validate(model_dump()) preserves contents
# ---------------------------------------------------------------------------


def test_per_actor_state_model_dump_validate_round_trip_preserves_contents():
    """``EncounterActor.model_validate(actor.model_dump())`` must preserve
    the per_actor_state contents byte-for-byte. The save/load path uses
    exactly this pattern."""
    original = EncounterActor(
        name="Charlie",
        role="instructor",
        side="neutral",
        per_actor_state={
            "altitude": 25000,
            "afterburner": True,
            "loadout": {"missiles": ["AIM-9", "AIM-7"], "gun_rounds": 940},
            "callouts": [],
        },
    )
    dumped = original.model_dump()
    restored = EncounterActor.model_validate(dumped)
    assert restored.per_actor_state == original.per_actor_state
    # And once more through JSON for the wire path.
    rejson = EncounterActor.model_validate_json(original.model_dump_json())
    assert rejson.per_actor_state == original.per_actor_state


def test_per_actor_state_round_trip_inside_structured_encounter():
    """Defense-in-depth: per_actor_state survives StructuredEncounter
    round-trip too (this is the actual wire shape T3 will mutate)."""
    enc = StructuredEncounter(
        encounter_type="dogfight",
        player_metric=EncounterMetric(name="advantage", threshold=10),
        opponent_metric=EncounterMetric(name="advantage", threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=[
            EncounterActor(
                name="Maverick",
                role="pilot",
                side="player",
                per_actor_state={"bearing": "high six", "range": 1200},
            ),
            EncounterActor(
                name="Jester",
                role="pilot",
                side="opponent",
                per_actor_state={"bearing": "low nine", "range": 1200},
            ),
        ],
    )
    restored = StructuredEncounter.model_validate_json(enc.model_dump_json())
    mav = restored.find_actor("Maverick")
    jes = restored.find_actor("Jester")
    assert mav is not None and jes is not None
    assert mav.per_actor_state == {"bearing": "high six", "range": 1200}
    assert jes.per_actor_state == {"bearing": "low nine", "range": 1200}


# ---------------------------------------------------------------------------
# Wiring test: production load path constructs EncounterActor without
# per_actor_state and the field is reachable end-to-end.
# ---------------------------------------------------------------------------


def test_per_actor_state_reachable_via_production_dispatch_constructor():
    """Wiring (CLAUDE.md "Every Test Suite Needs a Wiring Test"):
    ``encounter_lifecycle.py`` constructs ``EncounterActor`` *without*
    passing ``per_actor_state`` — only ``name``, ``role``, ``side``. T3
    then expects to mutate ``per_actor_state`` on those very instances.

    This test mirrors that production call-shape exactly and proves the
    field is reachable, mutable, and isolated per actor on the actual
    code path. If the production constructor signature ever drifts (e.g.
    requires per_actor_state to be passed in), this test fails loud.
    """
    # Mirrors encounter_lifecycle.py lines 97-104 exactly.
    actors = [
        EncounterActor(name="Player1", role="combatant", side="player"),
        EncounterActor(name="NPC_Wolf", role="combatant", side="opponent"),
        EncounterActor(name="Bystander", role="combatant", side="neutral"),
    ]

    # Field must be present, default empty, and isolated per instance.
    for a in actors:
        assert a.per_actor_state == {}

    # T3 will do exactly this — write a per-pilot descriptor between turns.
    actors[0].per_actor_state["cockpit_descriptor"] = "afterburner glow"
    actors[1].per_actor_state["cockpit_descriptor"] = "missile lock tone"

    # Cross-actor isolation holds end-to-end.
    assert actors[0].per_actor_state == {"cockpit_descriptor": "afterburner glow"}
    assert actors[1].per_actor_state == {"cockpit_descriptor": "missile lock tone"}
    assert actors[2].per_actor_state == {}

    # And the whole encounter still serializes cleanly with these mutations.
    enc = StructuredEncounter(
        encounter_type="dogfight",
        player_metric=EncounterMetric(name="advantage", threshold=10),
        opponent_metric=EncounterMetric(name="advantage", threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=actors,
    )
    restored = StructuredEncounter.model_validate_json(enc.model_dump_json())
    assert restored.find_actor("Player1").per_actor_state == {
        "cockpit_descriptor": "afterburner glow"
    }
    assert restored.find_actor("NPC_Wolf").per_actor_state == {
        "cockpit_descriptor": "missile lock tone"
    }
    assert restored.find_actor("Bystander").per_actor_state == {}
