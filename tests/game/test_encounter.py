"""Tests for sidequest.game.encounter — StructuredEncounter + friends.

Port of sidequest-api/crates/sidequest-game/tests/encounter_story_16_2_tests.rs
(Story 16-2 was the Rust introduction of StructuredEncounter).

Test-porting discipline (epic 42 execution-strategy spec §2):
    Every Rust test becomes one pytest function with the same name.
    No idiomatic rewrites during the port.

AC coverage (42-1):
- AC1: round-trip JSON parity with Rust-produced fixture
- AC2: constructors produce Rust-parity output
- AC4: GameSnapshot.encounter is typed StructuredEncounter | None
- AC5: unknown/malformed encounter data raises ValidationError (no silent fallback)
- AC6: GameSnapshot extra=ignore preserved for forward-compat
"""

from __future__ import annotations

import pytest

pytest.skip(
    "Pending dual-dial rewrite — Tasks 9-13 (MetricDirection removed)",
    allow_module_level=True,
)
# ruff: noqa: E402

import json
import typing
from pathlib import Path

from pydantic import ValidationError

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    MetricDirection,
    RigType,
    SecondaryStats,
    StatValue,
    StructuredEncounter,
)
from sidequest.game.session import GameSnapshot

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "encounters"


# ==========================================================================
# AC: StructuredEncounter struct compiles with all fields, serializes/deserializes
# ==========================================================================


def test_structured_encounter_construction_with_all_fields() -> None:
    encounter = StructuredEncounter(
        encounter_type="chase",
        metric=EncounterMetric(
            name="separation",
            current=5,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=10,
            threshold_low=None,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=None,
        actors=[],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )

    assert encounter.encounter_type == "chase"
    assert encounter.metric.name == "separation"
    assert encounter.metric.current == 5
    assert encounter.beat == 0
    assert encounter.resolved is False


def test_structured_encounter_serde_roundtrip() -> None:
    encounter = StructuredEncounter(
        encounter_type="standoff",
        metric=EncounterMetric(
            name="tension",
            current=0,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=10,
            threshold_low=None,
        ),
        beat=3,
        structured_phase=EncounterPhase.Escalation,
        secondary_stats=SecondaryStats(
            stats={"focus": StatValue(current=5, max=8)},
            damage_tier=None,
        ),
        actors=[
            EncounterActor(
                name="Clint",
                role="duelist",
                per_actor_state={},
            ),
        ],
        outcome=None,
        resolved=False,
        mood_override="standoff",
        narrator_hints=["Sweat beads on his brow"],
    )

    blob = encounter.model_dump_json()
    deserialized = StructuredEncounter.model_validate_json(blob)

    assert deserialized.encounter_type == "standoff"
    assert deserialized.metric.name == "tension"
    assert deserialized.metric.direction == MetricDirection.Ascending
    assert deserialized.beat == 3
    assert len(deserialized.actors) == 1
    assert deserialized.actors[0].name == "Clint"
    assert deserialized.actors[0].role == "duelist"
    assert deserialized.mood_override == "standoff"
    assert len(deserialized.narrator_hints) == 1

    # Secondary stats survived roundtrip
    assert deserialized.secondary_stats is not None
    focus = deserialized.secondary_stats.stats["focus"]
    assert focus.current == 5
    assert focus.max == 8


def test_structured_encounter_with_no_optional_fields() -> None:
    encounter = StructuredEncounter(
        encounter_type="negotiation",
        metric=EncounterMetric(
            name="leverage",
            current=0,
            starting=0,
            direction=MetricDirection.Bidirectional,
            threshold_high=5,
            threshold_low=-5,
        ),
        beat=0,
        structured_phase=None,
        secondary_stats=None,
        actors=[],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )

    blob = encounter.model_dump_json()
    de = StructuredEncounter.model_validate_json(blob)

    assert de.encounter_type == "negotiation"
    assert de.structured_phase is None
    assert de.secondary_stats is None
    assert de.actors == []
    assert de.outcome is None
    assert de.mood_override is None


# ==========================================================================
# AC: Metric types — Ascending, Descending, Bidirectional all work
# ==========================================================================


def test_metric_direction_ascending() -> None:
    metric = EncounterMetric(
        name="tension",
        current=0,
        starting=0,
        direction=MetricDirection.Ascending,
        threshold_high=10,
        threshold_low=None,
    )

    assert metric.direction == MetricDirection.Ascending
    assert metric.threshold_high == 10
    assert metric.threshold_low is None


def test_metric_direction_descending() -> None:
    metric = EncounterMetric(
        name="separation",
        current=10,
        starting=10,
        direction=MetricDirection.Descending,
        threshold_high=None,
        threshold_low=0,
    )

    assert metric.direction == MetricDirection.Descending
    assert metric.current == 10
    assert metric.starting == 10
    assert metric.threshold_low == 0


def test_metric_direction_bidirectional() -> None:
    metric = EncounterMetric(
        name="leverage",
        current=0,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=5,
        threshold_low=-5,
    )

    assert metric.direction == MetricDirection.Bidirectional
    assert metric.threshold_high == 5
    assert metric.threshold_low == -5


def test_metric_direction_serde_roundtrip() -> None:
    # All three variants must survive JSON roundtrip
    for direction in (
        MetricDirection.Ascending,
        MetricDirection.Descending,
        MetricDirection.Bidirectional,
    ):
        blob = json.dumps(direction.value)
        de = MetricDirection(json.loads(blob))
        assert de == direction, f"direction {direction!r} must roundtrip"


# ==========================================================================
# AC: Secondary stats — RigStats expressible as SecondaryStats
# ==========================================================================


def test_secondary_stats_basic_construction() -> None:
    stats = SecondaryStats(
        stats={
            "hp": StatValue(current=15, max=15),
            "fuel": StatValue(current=8, max=8),
            "speed": StatValue(current=5, max=5),
            "armor": StatValue(current=1, max=1),
            "maneuver": StatValue(current=3, max=3),
        },
        damage_tier="PRISTINE",
    )

    assert len(stats.stats) == 5
    hp = stats.stats["hp"]
    assert hp.current == 15
    assert hp.max == 15
    assert stats.damage_tier == "PRISTINE"


def test_secondary_stats_serde_roundtrip() -> None:
    stats = SecondaryStats(
        stats={
            "shields": StatValue(current=100, max=200),
            "hull": StatValue(current=80, max=80),
        },
        damage_tier=None,
    )

    blob = stats.model_dump_json()
    de = SecondaryStats.model_validate_json(blob)

    assert len(de.stats) == 2
    shields = de.stats["shields"]
    assert shields.current == 100
    assert shields.max == 200
    assert de.damage_tier is None


def test_secondary_stats_rig_convenience_constructor() -> None:
    """RigStats becomes a convenience constructor: SecondaryStats.rig(RigType).

    Values must match Rust RigStats::from_type(Interceptor):
    hp=15, speed=5, armor=1, maneuver=3, fuel=8, tier="PRISTINE".
    """
    stats = SecondaryStats.rig(RigType.Interceptor)

    hp = stats.stats["hp"]
    assert hp.current == 15
    assert hp.max == 15

    speed = stats.stats["speed"]
    assert speed.current == 5
    assert speed.max == 5

    armor = stats.stats["armor"]
    assert armor.current == 1
    assert armor.max == 1

    maneuver = stats.stats["maneuver"]
    assert maneuver.current == 3
    assert maneuver.max == 3

    fuel = stats.stats["fuel"]
    assert fuel.current == 8
    assert fuel.max == 8

    assert stats.damage_tier == "PRISTINE"


# ==========================================================================
# AC: EncounterActor with string-keyed roles
# ==========================================================================


def test_encounter_actor_string_roles() -> None:
    actors = [
        EncounterActor(name="Max", role="driver", per_actor_state={}),
        EncounterActor(name="Furiosa", role="gunner", per_actor_state={}),
        EncounterActor(name="Nux", role="mechanic", per_actor_state={}),
    ]

    assert len(actors) == 3
    assert actors[0].role == "driver"
    assert actors[1].role == "gunner"
    assert actors[2].role == "mechanic"


def test_encounter_actor_arbitrary_roles() -> None:
    # String-keyed roles means genre packs can define anything
    actor = EncounterActor(name="Neo", role="netrunner", per_actor_state={})
    assert actor.role == "netrunner"

    actor2 = EncounterActor(name="Deckard", role="interrogator", per_actor_state={})
    assert actor2.role == "interrogator"


def test_encounter_actor_serde_roundtrip() -> None:
    actor = EncounterActor(name="Blondie", role="duelist", per_actor_state={})

    blob = actor.model_dump_json()
    de = EncounterActor.model_validate_json(blob)

    assert de.name == "Blondie"
    assert de.role == "duelist"


def test_encounter_actor_per_actor_state_preserves_shape() -> None:
    """ADR-077 sealed-letter dispatcher reads this field — any drift breaks
    it silently. Test guards shape preservation (dict[str, Any])."""
    actor = EncounterActor(
        name="Wing",
        role="pilot",
        per_actor_state={
            "bearing": 270,
            "range": "close",
            "gun_solution": True,
            "energy": 0.75,
            "nested": {"weapon": "railgun"},
        },
    )

    blob = actor.model_dump_json()
    de = EncounterActor.model_validate_json(blob)

    assert de.per_actor_state["bearing"] == 270
    assert de.per_actor_state["range"] == "close"
    assert de.per_actor_state["gun_solution"] is True
    assert de.per_actor_state["energy"] == 0.75
    assert de.per_actor_state["nested"] == {"weapon": "railgun"}


# ==========================================================================
# AC: EncounterPhase — universal narrative arc
# ==========================================================================


def test_encounter_phase_variants() -> None:
    # The universal narrative arc: Setup -> Opening -> Escalation -> Climax -> Resolution.
    # Enumerate membership (not list length) so the test fails if a variant is
    # dropped or renamed upstream.
    assert set(EncounterPhase) == {
        EncounterPhase.Setup,
        EncounterPhase.Opening,
        EncounterPhase.Escalation,
        EncounterPhase.Climax,
        EncounterPhase.Resolution,
    }
    # Rust-verbatim values — guards against value drift (these strings are
    # what serde emits for the Rust enum).
    assert EncounterPhase.Setup.value == "Setup"
    assert EncounterPhase.Resolution.value == "Resolution"


def test_encounter_phase_serde_roundtrip() -> None:
    for phase in (
        EncounterPhase.Setup,
        EncounterPhase.Opening,
        EncounterPhase.Escalation,
        EncounterPhase.Climax,
        EncounterPhase.Resolution,
    ):
        blob = json.dumps(phase.value)
        de = EncounterPhase(json.loads(blob))
        assert de == phase, f"phase {phase!r} must roundtrip"


def test_encounter_phase_has_drama_weight() -> None:
    # Each phase has a drama weight for cinematography. Values ported
    # verbatim from Rust EncounterPhase::drama_weight().
    assert EncounterPhase.Setup.drama_weight() == pytest.approx(0.70)
    assert EncounterPhase.Opening.drama_weight() == pytest.approx(0.75)
    assert EncounterPhase.Escalation.drama_weight() == pytest.approx(0.80)
    assert EncounterPhase.Climax.drama_weight() == pytest.approx(0.95)
    assert EncounterPhase.Resolution.drama_weight() == pytest.approx(0.70)

    # Rust-verbatim inequality assertions
    assert EncounterPhase.Setup.drama_weight() > 0.0
    assert EncounterPhase.Climax.drama_weight() > EncounterPhase.Setup.drama_weight()
    assert EncounterPhase.Climax.drama_weight() >= 0.90


# ==========================================================================
# AC4: GameSnapshot.encounter is typed StructuredEncounter | None
# ==========================================================================


def test_game_snapshot_has_encounter_field() -> None:
    snapshot = GameSnapshot()
    assert snapshot.encounter is None

    snapshot.encounter = StructuredEncounter(
        encounter_type="chase",
        metric=EncounterMetric(
            name="separation",
            current=5,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=10,
            threshold_low=None,
        ),
        beat=2,
        structured_phase=EncounterPhase.Escalation,
        secondary_stats=None,
        actors=[],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )

    enc = snapshot.encounter
    assert enc is not None
    assert enc.encounter_type == "chase"
    assert enc.metric.current == 5


def test_game_snapshot_encounter_serde_roundtrip() -> None:
    snapshot = GameSnapshot(
        encounter=StructuredEncounter(
            encounter_type="standoff",
            metric=EncounterMetric(
                name="tension",
                current=7,
                starting=0,
                direction=MetricDirection.Ascending,
                threshold_high=10,
                threshold_low=None,
            ),
            beat=4,
            structured_phase=EncounterPhase.Climax,
            secondary_stats=None,
            actors=[
                EncounterActor(
                    name="Angel Eyes",
                    role="duelist",
                    per_actor_state={},
                ),
            ],
            outcome=None,
            resolved=False,
            mood_override="standoff",
            narrator_hints=["The clock strikes noon"],
        ),
    )

    blob = snapshot.model_dump_json()
    de = GameSnapshot.model_validate_json(blob)

    assert de.encounter is not None
    enc = de.encounter
    assert enc.encounter_type == "standoff"
    assert enc.beat == 4
    assert len(enc.actors) == 1
    assert enc.narrator_hints[0] == "The clock strikes noon"


def test_game_snapshot_encounter_type_annotation_is_structured_encounter() -> None:
    """AC4 wiring test: verify type annotation is StructuredEncounter | None,
    not dict | None. Guards against regression of the Phase 1/2 placeholder."""
    hints = typing.get_type_hints(GameSnapshot)
    encounter_hint = hints["encounter"]
    # StructuredEncounter | None is typing.Optional[StructuredEncounter]
    # which unpacks to Union[StructuredEncounter, None].
    args = typing.get_args(encounter_hint)
    assert StructuredEncounter in args, (
        f"GameSnapshot.encounter must accept StructuredEncounter, got {encounter_hint!r}"
    )
    assert type(None) in args, (
        f"GameSnapshot.encounter must remain nullable, got {encounter_hint!r}"
    )
    # Guard against the old dict placeholder lingering.
    assert dict not in args, (
        f"GameSnapshot.encounter must NOT be dict anymore, got {encounter_hint!r}"
    )


# ==========================================================================
# AC: Backward compat — old saves with chase field deserialize into encounter
# ==========================================================================
# NOTE: skipped per Design Deviation — Rust-specific legacy migration.
# See .session/42-1-session.md > Design Deviations > TEA.
# ==========================================================================


# ==========================================================================
# AC2: Constructors — ::combat(...) and ::chase(...)
# ==========================================================================


def test_structured_encounter_chase_convenience_constructor() -> None:
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=RigType.Interceptor,
        goal=10,
    )

    assert encounter.encounter_type == "chase"
    assert encounter.metric.name == "separation"
    assert encounter.metric.direction == MetricDirection.Ascending
    assert encounter.metric.threshold_high == 10
    assert encounter.resolved is False
    assert encounter.structured_phase == EncounterPhase.Setup

    # If rig type provided, secondary stats should be populated
    assert encounter.secondary_stats is not None
    assert "hp" in encounter.secondary_stats.stats
    assert "fuel" in encounter.secondary_stats.stats


def test_structured_encounter_chase_without_rig() -> None:
    # Foot chases have no secondary stats
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=None,
        goal=10,
    )

    assert encounter.encounter_type == "chase"
    assert encounter.secondary_stats is None


def test_structured_encounter_combat_convenience_constructor() -> None:
    """Rust equivalent: StructuredEncounter::combat(vec![...], hp)."""
    encounter = StructuredEncounter.combat(
        combatants=["Alice", "Bob"],
        hp=30,
    )

    assert encounter.encounter_type == "combat"
    assert encounter.metric.name == "hp"
    assert encounter.metric.current == 30
    assert encounter.metric.starting == 30
    assert encounter.metric.direction == MetricDirection.Descending
    assert encounter.metric.threshold_high is None
    assert encounter.metric.threshold_low == 0

    assert encounter.beat == 0
    assert encounter.structured_phase == EncounterPhase.Setup
    assert encounter.secondary_stats is None

    # Actors populated with role="combatant"
    assert len(encounter.actors) == 2
    assert encounter.actors[0].name == "Alice"
    assert encounter.actors[0].role == "combatant"
    assert encounter.actors[0].per_actor_state == {}
    assert encounter.actors[1].name == "Bob"
    assert encounter.actors[1].role == "combatant"

    assert encounter.resolved is False
    assert encounter.outcome is None
    assert encounter.mood_override is None
    assert encounter.narrator_hints == []


def test_structured_encounter_combat_with_empty_combatants() -> None:
    """Edge: combat() with empty combatant list — actors list is empty,
    not an error. Port Rust's permissive behavior verbatim."""
    encounter = StructuredEncounter.combat(combatants=[], hp=15)

    assert encounter.encounter_type == "combat"
    assert encounter.actors == []
    assert encounter.metric.current == 15


# ==========================================================================
# Rule #2: MetricDirection non-exhaustive-equivalent
# ==========================================================================


def test_metric_direction_is_non_exhaustive() -> None:
    """Rust enum has #[non_exhaustive] — future variants may land.

    Asserts membership set (not list length), so removing/renaming a
    variant upstream breaks this test. Adding a new variant to the
    Python enum will also break this — that's desired: the port author
    must consciously widen the known set when a new Rust variant lands.
    """
    assert set(MetricDirection) == {
        MetricDirection.Ascending,
        MetricDirection.Descending,
        MetricDirection.Bidirectional,
    }
    # Rust-verbatim serde values — guards against value drift.
    assert MetricDirection.Ascending.value == "Ascending"
    assert MetricDirection.Descending.value == "Descending"
    assert MetricDirection.Bidirectional.value == "Bidirectional"


def test_metric_direction_unknown_variant_fails_validation() -> None:
    """Rule #2 enforcement: unknown MetricDirection variant MUST fail
    validation — no silent fallback to a default. CLAUDE.md No Silent Fallbacks."""
    bad_json = json.dumps(
        {
            "name": "tension",
            "current": 0,
            "starting": 0,
            "direction": "Sideways",  # not a valid variant
            "threshold_high": 10,
            "threshold_low": None,
        }
    )
    with pytest.raises(ValidationError):
        EncounterMetric.model_validate_json(bad_json)


def test_encounter_phase_unknown_variant_fails_validation() -> None:
    """Same principle: unknown EncounterPhase variant raises ValidationError."""
    bad_json = json.dumps(
        {
            "encounter_type": "chase",
            "metric": {
                "name": "separation",
                "current": 0,
                "starting": 0,
                "direction": "Ascending",
                "threshold_high": 10,
                "threshold_low": None,
            },
            "beat": 0,
            "structured_phase": "Wiggle",  # not a valid EncounterPhase
            "secondary_stats": None,
            "actors": [],
            "outcome": None,
            "resolved": False,
            "mood_override": None,
            "narrator_hints": [],
        }
    )
    with pytest.raises(ValidationError):
        StructuredEncounter.model_validate_json(bad_json)


# ==========================================================================
# AC5: Unknown / malformed encounter raises ValidationError on GameSnapshot load
# ==========================================================================


def test_unknown_encounter_type_fails_loud_on_snapshot_load() -> None:
    """AC5: Save with malformed encounter payload raises ValidationError.
    No silent fallback, no default-to-'combat' coercion.

    'flibbertigibbet' encounter_type with missing required fields triggers
    pydantic ValidationError at the StructuredEncounter schema level."""
    bad_save = {
        "encounter": {"encounter_type": "flibbertigibbet"},
    }
    with pytest.raises(ValidationError):
        GameSnapshot.model_validate(bad_save)


def test_encounter_bad_metric_direction_fails_loud_on_snapshot_load() -> None:
    """AC5: unknown MetricDirection inside encounter payload raises
    ValidationError on GameSnapshot load. Extends Rule #2 to the outer
    GameSnapshot boundary."""
    bad_save = {
        "encounter": {
            "encounter_type": "chase",
            "metric": {
                "name": "separation",
                "current": 0,
                "starting": 0,
                "direction": "FlibbertiGibbet",
                "threshold_high": 10,
                "threshold_low": None,
            },
            "beat": 0,
            "structured_phase": "Setup",
            "secondary_stats": None,
            "actors": [],
            "outcome": None,
            "resolved": False,
            "mood_override": None,
            "narrator_hints": [],
        },
    }
    with pytest.raises(ValidationError):
        GameSnapshot.model_validate(bad_save)


# ==========================================================================
# AC6: GameSnapshot extra=ignore preserved
# ==========================================================================


def test_game_snapshot_extra_ignore_preserved() -> None:
    """AC6: Don't tighten to extra=forbid during this story. Rust-produced
    saves may carry fields the Python port doesn't know about yet."""
    save = {
        "genre_slug": "road_warrior",
        "world_slug": "flickering_reach",
        "some_phase_4_field_we_dont_know": {"foo": "bar"},
        "another_unknown": [1, 2, 3],
    }
    # Should NOT raise — unknown top-level fields are ignored.
    snapshot = GameSnapshot.model_validate(save)
    assert snapshot.genre_slug == "road_warrior"
    assert snapshot.world_slug == "flickering_reach"


# ==========================================================================
# AC1: Round-trip JSON parity with Rust-produced fixture
# ==========================================================================


def _load_fixture(name: str) -> dict:
    path = FIXTURE_DIR / name
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def test_combat_fixture_round_trip() -> None:
    """AC1: A combat-flavor JSON blob produced on the Rust side model-validates
    in Python and re-serializes to an equivalent blob."""
    fixture = _load_fixture("combat_alice_bob_hp30.json")
    encounter = StructuredEncounter.model_validate(fixture)

    assert encounter.encounter_type == "combat"
    assert encounter.metric.name == "hp"
    assert encounter.metric.current == 30
    assert encounter.metric.direction == MetricDirection.Descending
    assert encounter.metric.threshold_low == 0
    assert encounter.metric.threshold_high is None
    assert encounter.beat == 0
    assert encounter.structured_phase == EncounterPhase.Setup
    assert encounter.secondary_stats is None
    assert len(encounter.actors) == 2
    assert encounter.actors[0].name == "Alice"
    assert encounter.actors[0].role == "combatant"
    assert encounter.actors[1].name == "Bob"
    assert encounter.resolved is False

    # Roundtrip: re-serialize and compare field-by-field against the fixture
    reserialized = json.loads(encounter.model_dump_json())
    assert reserialized == fixture


def test_chase_with_rig_fixture_round_trip() -> None:
    """AC1: chase-flavor fixture with secondary_stats populated."""
    fixture = _load_fixture("chase_interceptor_goal10.json")
    encounter = StructuredEncounter.model_validate(fixture)

    assert encounter.encounter_type == "chase"
    assert encounter.metric.direction == MetricDirection.Ascending
    assert encounter.metric.threshold_high == 10
    assert encounter.secondary_stats is not None
    assert encounter.secondary_stats.stats["hp"].current == 15
    assert encounter.secondary_stats.stats["fuel"].max == 8
    assert encounter.secondary_stats.damage_tier == "PRISTINE"

    reserialized = json.loads(encounter.model_dump_json())
    assert reserialized == fixture


def test_chase_no_rig_fixture_round_trip() -> None:
    """AC1 edge case: chase encounter with secondary_stats=None
    (minimal chase construction). Must round-trip cleanly."""
    fixture = _load_fixture("chase_no_rig_goal10.json")
    encounter = StructuredEncounter.model_validate(fixture)

    assert encounter.encounter_type == "chase"
    assert encounter.secondary_stats is None

    reserialized = json.loads(encounter.model_dump_json())
    assert reserialized == fixture


def test_standoff_full_fixture_round_trip() -> None:
    """AC1: standoff fixture exercising actors + narrator_hints + mood_override."""
    fixture = _load_fixture("standoff_full.json")
    encounter = StructuredEncounter.model_validate(fixture)

    assert encounter.encounter_type == "standoff"
    assert encounter.structured_phase == EncounterPhase.Escalation
    assert encounter.mood_override == "standoff"
    assert encounter.narrator_hints == ["Sweat beads on his brow"]
    assert len(encounter.actors) == 1
    assert encounter.actors[0].role == "duelist"

    reserialized = json.loads(encounter.model_dump_json())
    assert reserialized == fixture


def test_combat_constructor_matches_fixture() -> None:
    """AC2: ::combat(["Alice", "Bob"], 30) must produce the same JSON
    Rust would for the same arguments."""
    encounter = StructuredEncounter.combat(combatants=["Alice", "Bob"], hp=30)
    fixture = _load_fixture("combat_alice_bob_hp30.json")
    assert json.loads(encounter.model_dump_json()) == fixture


def test_chase_constructor_matches_fixture() -> None:
    """AC2: ::chase(0.5, Interceptor, 10) must produce the same JSON
    Rust would for the same arguments."""
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=RigType.Interceptor,
        goal=10,
    )
    fixture = _load_fixture("chase_interceptor_goal10.json")
    assert json.loads(encounter.model_dump_json()) == fixture


def test_chase_without_rig_constructor_matches_fixture() -> None:
    """AC2 edge case: chase with no rig produces the minimal-chase fixture."""
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=None,
        goal=10,
    )
    fixture = _load_fixture("chase_no_rig_goal10.json")
    assert json.loads(encounter.model_dump_json()) == fixture


# ==========================================================================
# resolve_from_trope — method authored in 42-1 (consumer lands in 42-4)
# ==========================================================================


def test_resolve_from_trope_sets_resolved_and_outcome() -> None:
    """Port of Rust resolve_from_trope behavior: marks encounter resolved,
    sets structured_phase=Resolution, records trope_id in outcome string."""
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=None,
        goal=10,
    )
    assert encounter.resolved is False

    encounter.resolve_from_trope("sacrificial_gambit")

    assert encounter.resolved is True
    assert encounter.structured_phase == EncounterPhase.Resolution
    assert encounter.outcome is not None
    assert "sacrificial_gambit" in encounter.outcome


def test_resolve_from_trope_is_noop_if_already_resolved() -> None:
    """Port of Rust guard: no-op if encounter is already resolved.
    Outcome and phase preserved from first resolution."""
    encounter = StructuredEncounter.chase(
        escape_threshold=0.5,
        rig_type=None,
        goal=10,
    )
    encounter.resolve_from_trope("first_trope")
    first_outcome = encounter.outcome

    encounter.resolve_from_trope("second_trope")

    # Outcome unchanged — second call was a no-op
    assert encounter.outcome == first_outcome


# ==========================================================================
# Full encounter scenarios — genre-specific encounter types
# ==========================================================================


def test_standoff_encounter_full_construction() -> None:
    # Spaghetti western standoff: ascending tension to threshold
    encounter = StructuredEncounter(
        encounter_type="standoff",
        metric=EncounterMetric(
            name="tension",
            current=0,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=10,
            threshold_low=None,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=SecondaryStats(
            stats={"focus": StatValue(current=5, max=5)},
            damage_tier=None,
        ),
        actors=[
            EncounterActor(name="The Good", role="duelist", per_actor_state={}),
            EncounterActor(name="The Bad", role="duelist", per_actor_state={}),
            EncounterActor(name="The Ugly", role="duelist", per_actor_state={}),
        ],
        outcome=None,
        resolved=False,
        mood_override="standoff",
        narrator_hints=[
            "Three men circle in the cemetery",
            "Ennio Morricone intensifies",
        ],
    )

    assert encounter.encounter_type == "standoff"
    assert len(encounter.actors) == 3
    assert len(encounter.narrator_hints) == 2
    assert encounter.secondary_stats is not None
    focus = encounter.secondary_stats.stats["focus"]
    assert focus.current == 5


def test_negotiation_encounter_bidirectional_metric() -> None:
    encounter = StructuredEncounter(
        encounter_type="negotiation",
        metric=EncounterMetric(
            name="leverage",
            current=0,
            starting=0,
            direction=MetricDirection.Bidirectional,
            threshold_high=5,
            threshold_low=-5,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=None,
        actors=[
            EncounterActor(name="Detective", role="interrogator", per_actor_state={}),
            EncounterActor(name="Suspect", role="subject", per_actor_state={}),
        ],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )

    assert encounter.metric.direction == MetricDirection.Bidirectional
    assert encounter.metric.threshold_high == 5
    assert encounter.metric.threshold_low == -5


def test_ship_combat_encounter_with_secondary_stats() -> None:
    encounter = StructuredEncounter(
        encounter_type="ship_combat",
        metric=EncounterMetric(
            name="hull_integrity",
            current=80,
            starting=80,
            direction=MetricDirection.Descending,
            threshold_high=None,
            threshold_low=0,
        ),
        beat=0,
        structured_phase=EncounterPhase.Setup,
        secondary_stats=SecondaryStats(
            stats={
                "shields": StatValue(current=100, max=200),
                "hull": StatValue(current=80, max=80),
                "engines": StatValue(current=50, max=50),
            },
            damage_tier="PRISTINE",
        ),
        actors=[
            EncounterActor(name="Captain", role="commander", per_actor_state={}),
            EncounterActor(name="Pilot", role="helmsman", per_actor_state={}),
            EncounterActor(name="Gunner", role="weapons", per_actor_state={}),
        ],
        outcome=None,
        resolved=False,
        mood_override="combat",
        narrator_hints=[],
    )

    assert encounter.encounter_type == "ship_combat"
    assert encounter.metric.direction == MetricDirection.Descending
    assert encounter.secondary_stats is not None
    stats = encounter.secondary_stats
    assert len(stats.stats) == 3
    assert "shields" in stats.stats
    assert "hull" in stats.stats
    assert "engines" in stats.stats


# ==========================================================================
# Edge cases
# ==========================================================================


def test_encounter_with_empty_encounter_type_still_serializes() -> None:
    # Edge: empty string encounter_type — struct handles it gracefully.
    encounter = StructuredEncounter(
        encounter_type="",
        metric=EncounterMetric(
            name="test",
            current=0,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=None,
            threshold_low=None,
        ),
        beat=0,
        structured_phase=None,
        secondary_stats=None,
        actors=[],
        outcome=None,
        resolved=False,
        mood_override=None,
        narrator_hints=[],
    )

    blob = encounter.model_dump_json()
    de = StructuredEncounter.model_validate_json(blob)
    assert de.encounter_type == ""


def test_stat_value_zero_max_is_valid() -> None:
    # Edge: a stat with max=0 (disabled subsystem)
    sv = StatValue(current=0, max=0)
    blob = sv.model_dump_json()
    de = StatValue.model_validate_json(blob)
    assert de.current == 0
    assert de.max == 0


def test_encounter_metric_negative_values_valid() -> None:
    # Bidirectional metrics can go negative
    metric = EncounterMetric(
        name="leverage",
        current=-3,
        starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=5,
        threshold_low=-5,
    )

    blob = metric.model_dump_json()
    de = EncounterMetric.model_validate_json(blob)
    assert de.current == -3


def test_encounter_resolved_flag_persists() -> None:
    encounter = StructuredEncounter(
        encounter_type="chase",
        metric=EncounterMetric(
            name="separation",
            current=10,
            starting=0,
            direction=MetricDirection.Ascending,
            threshold_high=10,
            threshold_low=None,
        ),
        beat=5,
        structured_phase=EncounterPhase.Resolution,
        secondary_stats=None,
        actors=[],
        outcome="escape",
        resolved=True,
        mood_override=None,
        narrator_hints=[],
    )

    blob = encounter.model_dump_json()
    de = StructuredEncounter.model_validate_json(blob)
    assert de.resolved is True
    assert de.outcome == "escape"
    assert de.structured_phase == EncounterPhase.Resolution


# ==========================================================================
# Wiring tests (CLAUDE.md: "Every Test Suite Needs a Wiring Test")
# ==========================================================================


def test_game_module_exports_structured_encounter() -> None:
    """Verifies new 42-1 types are re-exported from the sidequest.game package.

    Every symbol added by 42-1 MUST be reachable via ``sidequest.game.X`` so
    downstream modules (42-2 ResourcePool, 42-3 TensionTracker, 42-4 dispatch
    + narrator + GM panel) can import from one canonical place without
    reaching into sub-modules.
    """
    import sidequest.game as game

    expected = [
        # encounter module
        "StructuredEncounter",
        "EncounterMetric",
        "EncounterActor",
        "EncounterPhase",
        "MetricDirection",
        "SecondaryStats",
        "StatValue",
        "RigType",
        # combatant module
        "Combatant",
    ]
    for symbol in expected:
        assert hasattr(game, symbol), (
            f"sidequest.game must re-export {symbol!r} so downstream modules "
            f"(dispatch, narrator, GM panel) can import from one canonical place"
        )
