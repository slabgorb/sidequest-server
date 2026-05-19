"""compose_room_prose — deterministic per-room composition (Story 55-1).

Covers AC-3 (determinism), AC-4 (dressing → flavor_only),
AC-5 (special → real_object with binding + affordances),
AC-6 (empty dressing raises ValueError), and the entity-id uniqueness
invariant the manifest contract relies on.
"""

from __future__ import annotations

import random

import pytest

from sidequest.game.cookbook.compose import compose_room_prose
from sidequest.game.cookbook.models import GeneratedRoomDescription, LookDef, SpecialRoom


def _look(*, dressing: list[str], look_id: str = "damp_cavern") -> LookDef:
    return LookDef(
        id=look_id,
        generator_binding="cellular",
        register="grim",
        dressing=dressing,
    )


def _special(
    *,
    id: str = "echoing_pool",
    telegraph: str = "a black pool reflects the torchlight",
    mechanic: str = "drink_to_scry",
) -> SpecialRoom:
    return SpecialRoom(
        id=id,
        telegraph=telegraph,
        mechanic=mechanic,
        outcome="vision",
        min_band="shallow",
    )


def _rng(seed: int = 42) -> random.Random:
    r = random.Random()
    r.seed(seed)
    return r


# ---------------------------------------------------------------------------
# Return-shape contract
# ---------------------------------------------------------------------------


def test_returns_generated_room_description() -> None:
    look = _look(dressing=["A slick green crust coats the walls."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[],
        room_id="region_42",
    )
    assert isinstance(result, GeneratedRoomDescription)
    assert result.description, "compose must emit non-empty prose"


def test_room_id_is_propagated_to_result() -> None:
    look = _look(dressing=["A slick green crust coats the walls."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[],
        room_id="region_99",
    )
    assert result.room_id == "region_99"


# ---------------------------------------------------------------------------
# AC-4: dressing lines become flavor_only entities, no binding, sample 2-3
# ---------------------------------------------------------------------------


def test_dressing_lines_become_flavor_only_entities() -> None:
    look = _look(
        dressing=[
            "A slick green crust coats the walls.",
            "Water drips in regular plinks.",
            "The air smells of old iron.",
            "Skittering echoes recede behind the player.",
        ]
    )
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[],
        room_id="region_42",
    )
    flavor = [e for e in result.entities if e.tier == "flavor_only"]
    assert len(flavor) >= 1
    for e in flavor:
        assert e.provenance == "cookbook"
        assert e.binding is None, (
            f"flavor_only entity {e.id!r} must have no binding "
            "(only real_object entities bind to subsystem refs)."
        )


def test_dressing_sample_size_is_two_or_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4: spec §8 — assembler samples 2-3 dressing lines per room.

    Run several seeds; every result must pick between 2 and 3 flavor_only
    entities (bounded above by the pool size).
    """
    look = _look(dressing=[f"Line {i}." for i in range(20)])
    for seed in range(20):
        result = compose_room_prose(
            rng=_rng(seed=seed),
            look_def=look,
            special_rooms=[],
            room_id="region_42",
        )
        flavor = [e for e in result.entities if e.tier == "flavor_only"]
        assert 2 <= len(flavor) <= 3, (
            f"spec §8 violation at seed={seed}: got {len(flavor)} "
            f"flavor_only entities, expected 2 or 3 (pool size 20). "
            f"entities={[e.id for e in flavor]}"
        )


def test_dressing_sample_clamps_to_pool_size() -> None:
    """If the dressing pool is smaller than the minimum sample size (2),
    compose must clamp rather than crash. A pool of exactly 1 must yield
    exactly 1 flavor_only entity (no over-sampling, no duplicate)."""
    look = _look(dressing=["The only line."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[],
        room_id="region_42",
    )
    flavor = [e for e in result.entities if e.tier == "flavor_only"]
    assert len(flavor) == 1
    assert flavor[0].label == "The only line."


# ---------------------------------------------------------------------------
# AC-5: SpecialRoom → real_object entity with binding + affordances
# ---------------------------------------------------------------------------


def test_special_room_becomes_real_object_entity() -> None:
    look = _look(dressing=["A slick green crust coats the walls."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[_special(id="echoing_pool", mechanic="drink_to_scry")],
        room_id="region_42",
    )
    real_objects = [e for e in result.entities if e.tier == "real_object"]
    assert len(real_objects) == 1
    e = real_objects[0]
    assert e.provenance == "cookbook"
    assert e.binding is not None, (
        "AC-5: real_object entity from a SpecialRoom MUST carry a binding."
    )
    assert e.binding.kind == "location_feature"
    assert e.binding.ref == "echoing_pool"
    # AC-5: affordances seeded from SpecialRoom.mechanic.
    assert "drink_to_scry" in e.affordances


def test_special_room_telegraph_appears_in_prose() -> None:
    """The narrator hint surfaces in the description so the player
    actually sees the bait (Diamonds and Coal — a baited hook needs to
    be visible)."""
    look = _look(dressing=["A slick green crust coats the walls."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[
            _special(
                id="echoing_pool",
                telegraph="a black pool reflects the torchlight",
            )
        ],
        room_id="region_42",
    )
    assert "black pool reflects the torchlight" in result.description


def test_multiple_specials_each_become_one_real_object() -> None:
    look = _look(dressing=["A slick green crust coats the walls."])
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[
            _special(id="echoing_pool", mechanic="drink_to_scry"),
            _special(id="ancient_alter", mechanic="bleed_to_open"),
        ],
        room_id="region_42",
    )
    real_objects = [e for e in result.entities if e.tier == "real_object"]
    assert len(real_objects) == 2
    refs = {e.binding.ref for e in real_objects if e.binding is not None}
    assert refs == {"echoing_pool", "ancient_alter"}


# ---------------------------------------------------------------------------
# AC-3: determinism
# ---------------------------------------------------------------------------


def test_deterministic_given_same_inputs() -> None:
    look = _look(
        dressing=[
            "Line A.",
            "Line B.",
            "Line C.",
            "Line D.",
            "Line E.",
        ]
    )
    a = compose_room_prose(
        rng=_rng(seed=1234),
        look_def=look,
        special_rooms=[_special(id="echoing_pool")],
        room_id="region_42",
    )
    b = compose_room_prose(
        rng=_rng(seed=1234),
        look_def=look,
        special_rooms=[_special(id="echoing_pool")],
        room_id="region_42",
    )
    assert a.description == b.description
    assert [e.id for e in a.entities] == [e.id for e in b.entities]
    # Full model_dump equality so binding + affordances also stable.
    assert a.model_dump() == b.model_dump()


def test_different_seeds_produce_different_compositions() -> None:
    look = _look(dressing=[f"Line {i}." for i in range(20)])
    a = compose_room_prose(
        rng=_rng(seed=1),
        look_def=look,
        special_rooms=[],
        room_id="region_42",
    )
    b = compose_room_prose(
        rng=_rng(seed=999),
        look_def=look,
        special_rooms=[],
        room_id="region_42",
    )
    assert a.description != b.description or [e.id for e in a.entities] != [
        e.id for e in b.entities
    ]


# ---------------------------------------------------------------------------
# AC-6: empty dressing pool raises ValueError (No Silent Fallbacks)
# ---------------------------------------------------------------------------


def test_empty_dressing_pool_raises_loudly() -> None:
    """AC-6: a LookDef with no dressing means the bundle failed validation
    upstream. Compose refuses to fabricate prose."""
    look = _look(dressing=[])
    with pytest.raises(ValueError, match=r"dressing"):
        compose_room_prose(
            rng=_rng(),
            look_def=look,
            special_rooms=[],
            room_id="region_42",
        )


def test_empty_dressing_error_names_the_look_id() -> None:
    """Loud error must name the offending LookDef so dev sees WHICH
    bundle entry needs content (CLAUDE.md No Silent Fallbacks — surface
    actionable detail)."""
    look = _look(dressing=[], look_id="bad_cavern")
    with pytest.raises(ValueError) as excinfo:
        compose_room_prose(
            rng=_rng(),
            look_def=look,
            special_rooms=[],
            room_id="region_42",
        )
    assert "bad_cavern" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Entity-id uniqueness (manifest contract — 54-2 validator hard error
# on duplicate ids; cookbook output must satisfy it without the
# validator being involved as a fix-up layer)
# ---------------------------------------------------------------------------


def test_entity_ids_are_unique_within_a_room() -> None:
    look = _look(
        dressing=[
            "A slick green crust coats the walls.",
            "Water drips in regular plinks.",
            "The air smells of old iron.",
            "A slick green crust coats the walls.",  # duplicate text — same id
        ]
    )
    result = compose_room_prose(
        rng=_rng(),
        look_def=look,
        special_rooms=[
            _special(id="echoing_pool"),
            _special(id="echoing_pool"),  # duplicate id
        ],
        room_id="region_42",
    )
    ids = [e.id for e in result.entities]
    assert len(ids) == len(set(ids)), (
        f"compose must dedupe entity ids within a room; got {ids}"
    )
