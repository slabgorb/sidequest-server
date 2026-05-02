"""Tests for sidequest.game.resource_pool — ResourcePool + threshold-lore minting.

Port of two Rust integration-test files for ADR-033 resource pools:

- ``sidequest-api/crates/sidequest-game/tests/resource_pool_story_16_10_tests.rs``
- ``sidequest-api/crates/sidequest-game/tests/resource_threshold_knownfact_story_16_11_tests.rs``

Port discipline: every Rust ``#[test]`` becomes one pytest function with the
same name. No idiomatic rewrites — the Rust source is the behavioural contract.

Skips (Design Deviations, not tests we'd otherwise run):

- ``resource_pool_yaml_roundtrip``,
  ``resource_pool_from_yaml_with_thresholds``,
  ``resource_pool_from_yaml_without_thresholds_defaults_empty`` —
  YAML is not a Pydantic-native surface; the JSON round-trip tests
  cover the same serde shape. Matches 42-1 precedent.
- ``resource_pool_derives_clone_debug`` — Python's dataclass / pydantic
  ``model_copy`` and ``repr()`` are trivially satisfied; the test is a
  Rust-derive sanity check with no Python analogue.
- ``threshold_lore_appears_in_narrator_context_selection``,
  ``threshold_lore_prioritized_when_event_category_requested``,
  ``end_to_end_patch_to_narrator_context`` — these Rust tests exercise
  ``select_lore_for_prompt``, which is not ported to Python yet. Skip
  with deviation; add back when the narrator-context-selection slice
  lands. ``LoreStore.query_by_category`` IS ported and covers the
  load-bearing contract for 42-2 (minted fragment is retrievable in
  the Event category).

GameSnapshot method surface expected on Dev's implementation:

- ``GameSnapshot.apply_resource_patch(patch) -> ResourcePatchResult``
- ``GameSnapshot.apply_resource_patch_player(patch) -> ResourcePatchResult``
- ``GameSnapshot.apply_pool_decay() -> list[ResourceThreshold]``
- ``GameSnapshot.init_resource_pools(declarations) -> None``
- ``GameSnapshot.apply_resource_patch_by_name(name, op, value) -> ResourcePatchResult``
- ``GameSnapshot.process_resource_patch_with_lore(name, op, value, store, turn) -> ResourcePatchResult``

Dev can implement these as methods on ``GameSnapshot`` or as free
functions imported/bound — tests only care about the call shape.
"""

from __future__ import annotations

import json

import pytest

from sidequest.game.resource_pool import (
    ResourcePatch,
    ResourcePatchError,
    ResourcePatchOp,
    ResourcePatchResult,
    ResourcePool,
    ResourceThreshold,
)
from sidequest.game.session import GameSnapshot

# ---------------------------------------------------------------------------
# Test helpers — mirror Rust test-helper shapes verbatim
# ---------------------------------------------------------------------------


def make_pool(name: str, current: float, min_: float, max_: float) -> ResourcePool:
    return ResourcePool(
        name=name,
        label=name,
        current=current,
        min=min_,
        max=max_,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=[],
    )


def make_pool_with_thresholds(
    name: str,
    current: float,
    min_: float,
    max_: float,
    thresholds: list[ResourceThreshold],
) -> ResourcePool:
    return ResourcePool(
        name=name,
        label=name,
        current=current,
        min=min_,
        max=max_,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=thresholds,
    )


def make_threshold(at: float, event_id: str, hint: str) -> ResourceThreshold:
    return ResourceThreshold(at=at, event_id=event_id, narrator_hint=hint)


def snapshot_with_pools(pools: list[ResourcePool]) -> GameSnapshot:
    snap = GameSnapshot()
    for pool in pools:
        snap.resources[pool.name] = pool
    return snap


# ===========================================================================
# AC1: ResourcePool struct serializes/deserializes (serde parity)
# ===========================================================================


def test_resource_pool_json_roundtrip() -> None:
    pool = ResourcePool(
        name="luck",
        label="Luck",
        current=3.0,
        min=0.0,
        max=6.0,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=[
            ResourceThreshold(
                at=1.0,
                event_id="luck_critical",
                narrator_hint="Luck is nearly exhausted.",
            ),
            ResourceThreshold(
                at=0.0,
                event_id="luck_depleted",
                narrator_hint="Out of luck entirely.",
            ),
        ],
    )

    restored = ResourcePool.model_validate_json(pool.model_dump_json())

    assert restored.name == "luck"
    assert restored.current == pytest.approx(3.0)
    assert restored.min == pytest.approx(0.0)
    assert restored.max == pytest.approx(6.0)
    assert restored.voluntary is True
    assert restored.decay_per_turn == pytest.approx(0.0)
    assert len(restored.thresholds) == 2
    assert restored.thresholds[0].event_id == "luck_critical"
    assert restored.thresholds[0].at == pytest.approx(1.0)
    assert restored.thresholds[1].event_id == "luck_depleted"


def test_resource_threshold_json_roundtrip() -> None:
    threshold = ResourceThreshold(
        at=1.0,
        event_id="luck_critical",
        narrator_hint="Running low on luck.",
    )

    restored = ResourceThreshold.model_validate_json(threshold.model_dump_json())

    assert restored.at == pytest.approx(1.0)
    assert restored.event_id == "luck_critical"
    assert restored.narrator_hint == "Running low on luck."


# ---------------------------------------------------------------------------
# AC1: GameSnapshot with resources HashMap (Python: dict[str, ResourcePool])
# ---------------------------------------------------------------------------


def test_game_snapshot_resources_default_empty() -> None:
    snap = GameSnapshot()
    assert snap.resources == {}, "default should have no resource pools"


def test_game_snapshot_resources_json_roundtrip() -> None:
    snap = GameSnapshot()
    snap.resources["luck"] = make_pool("luck", 3.0, 0.0, 6.0)
    snap.resources["heat"] = make_pool("heat", 0.0, 0.0, 10.0)

    restored = GameSnapshot.model_validate_json(snap.model_dump_json())

    assert len(restored.resources) == 2
    assert "luck" in restored.resources
    assert "heat" in restored.resources
    assert restored.resources["luck"].current == pytest.approx(3.0)


def test_old_save_without_resources_field_deserializes() -> None:
    """AC5 — Old saves (missing ``resources``) must default to empty dict,
    matching Rust's ``#[serde(default)]`` on ``GameSnapshot.resources``."""
    # Minimal save — many P2-deferred fields, NO resources key.
    payload = {
        "genre_slug": "spaghetti_western",
        "world_slug": "dusty_gulch",
        "characters": [],
        "npcs": [],
        "location": "Saloon",
        "time_of_day": "high_noon",
        "quest_log": {},
        "notes": [],
        "narrative_log": [],
        "atmosphere": "tense",
        "current_region": "town",
        "discovered_regions": [],
        "discovered_routes": [],
    }

    snapshot = GameSnapshot.model_validate(payload)
    assert snapshot.resources == {}, "old saves without resources should default to empty dict"


# ===========================================================================
# AC3: ResourcePatch applies changes and clamps bounds
# ===========================================================================


def test_resource_patch_add_increases_value() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Add,
        value=2.0,
    )
    result = snap.apply_resource_patch(patch)

    assert isinstance(result, ResourcePatchResult), "valid add should return a ResourcePatchResult"
    assert snap.resources["luck"].current == pytest.approx(5.0), (
        "luck should be 5.0 after adding 2.0"
    )


def test_resource_patch_subtract_decreases_value() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    snap.apply_resource_patch(patch)

    assert snap.resources["luck"].current == pytest.approx(2.0)


def test_resource_patch_set_replaces_value() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Set,
        value=5.0,
    )
    snap.apply_resource_patch(patch)

    assert snap.resources["luck"].current == pytest.approx(5.0)


def test_resource_patch_clamps_to_max() -> None:
    pool = make_pool("luck", 5.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Add,
        value=10.0,
    )
    snap.apply_resource_patch(patch)

    assert snap.resources["luck"].current == pytest.approx(6.0), "should clamp to max, not raise"


def test_resource_patch_clamps_to_min() -> None:
    pool = make_pool("luck", 2.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=10.0,
    )
    snap.apply_resource_patch(patch)

    assert snap.resources["luck"].current == pytest.approx(0.0), "should clamp to min, not raise"


def test_resource_patch_set_rejects_below_min() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Set,
        value=-5.0,
    )
    snap.apply_resource_patch(patch)

    # Rust behaviour: set below min clamps to min (does NOT raise).
    assert snap.resources["luck"].current == pytest.approx(0.0)


def test_resource_patch_set_rejects_above_max() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Set,
        value=100.0,
    )
    snap.apply_resource_patch(patch)

    # Rust behaviour: set above max clamps to max (does NOT raise).
    assert snap.resources["luck"].current == pytest.approx(6.0)


def test_resource_patch_unknown_resource_returns_error() -> None:
    snap = GameSnapshot()

    patch = ResourcePatch(
        resource_name="nonexistent",
        operation=ResourcePatchOp.Add,
        value=1.0,
    )
    with pytest.raises(ResourcePatchError):
        snap.apply_resource_patch(patch)


def test_resource_patch_does_not_modify_state_on_error() -> None:
    """AC2: atomicity — failed patch must leave all pool state unchanged."""
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="nonexistent",
        operation=ResourcePatchOp.Add,
        value=1.0,
    )
    with pytest.raises(ResourcePatchError):
        snap.apply_resource_patch(patch)

    assert snap.resources["luck"].current == pytest.approx(3.0), (
        "failed patch must not modify any resource state"
    )


# ---------------------------------------------------------------------------
# AC3: ResourcePatch serde (JSON) parity
# ---------------------------------------------------------------------------


def test_resource_patch_json_roundtrip() -> None:
    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=2.0,
    )

    restored = ResourcePatch.model_validate_json(patch.model_dump_json())

    assert restored.resource_name == "luck"
    assert restored.operation is ResourcePatchOp.Subtract
    assert restored.value == pytest.approx(2.0)


def test_resource_patch_op_all_variants_serialize() -> None:
    """Rust uses ``#[serde(rename_all = \"lowercase\")]`` — variants serialize
    as ``"add"``, ``"subtract"``, ``"set"``."""
    for op, expected in [
        (ResourcePatchOp.Add, "add"),
        (ResourcePatchOp.Subtract, "subtract"),
        (ResourcePatchOp.Set, "set"),
    ]:
        # The operation field is the ground truth; dumping a patch with
        # this op embeds the serialized form.
        patch = ResourcePatch(resource_name="x", operation=op, value=0.0)
        dumped = json.loads(patch.model_dump_json())
        assert dumped["operation"].lower() == expected, (
            f"ResourcePatchOp.{op.name} should serialize as {expected!r}, got {dumped['operation']!r}"
        )


# ===========================================================================
# AC5 / AC3 combined: threshold-crossing detection (downward only)
# ===========================================================================


def test_threshold_crossing_detected_on_subtract() -> None:
    pool = make_pool_with_thresholds(
        "luck",
        3.0,
        0.0,
        6.0,
        [make_threshold(1.0, "luck_critical", "Nearly out of luck.")],
    )
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=2.5,
    )
    result = snap.apply_resource_patch(patch)

    # Value went from 3.0 to 0.5 — crossed threshold at 1.0
    assert len(result.crossed_thresholds) >= 1, "should detect crossing threshold at 1.0"
    assert result.crossed_thresholds[0].event_id == "luck_critical"


def test_threshold_not_crossed_when_still_above() -> None:
    pool = make_pool_with_thresholds(
        "luck",
        3.0,
        0.0,
        6.0,
        [make_threshold(1.0, "luck_critical", "Nearly out of luck.")],
    )
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    result = snap.apply_resource_patch(patch)

    # Value went from 3.0 to 2.0 — still above threshold at 1.0
    assert result.crossed_thresholds == []


def test_multiple_thresholds_crossed_in_single_patch() -> None:
    pool = make_pool_with_thresholds(
        "luck",
        5.0,
        0.0,
        6.0,
        [
            make_threshold(3.0, "luck_low", "Luck is running thin."),
            make_threshold(1.0, "luck_critical", "Nearly out of luck."),
        ],
    )
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=4.5,
    )
    result = snap.apply_resource_patch(patch)

    # Value went 5.0 → 0.5 — crossed 3.0 and 1.0
    assert len(result.crossed_thresholds) == 2
    event_ids = [t.event_id for t in result.crossed_thresholds]
    assert "luck_low" in event_ids
    assert "luck_critical" in event_ids


def test_threshold_not_re_triggered_when_already_below() -> None:
    """AC3 edge: a threshold only fires on **crossing** (old > at, new <= at).
    If we're already below and drop further, no re-fire."""
    pool = make_pool_with_thresholds(
        "luck",
        0.5,
        0.0,
        6.0,
        [make_threshold(1.0, "luck_critical", "Nearly out of luck.")],
    )
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=0.2,
    )
    result = snap.apply_resource_patch(patch)

    assert result.crossed_thresholds == [], (
        "threshold must not re-fire when value was already below it"
    )


def test_threshold_crossing_on_set_operation() -> None:
    pool = make_pool_with_thresholds(
        "luck",
        5.0,
        0.0,
        6.0,
        [make_threshold(2.0, "luck_low", "Running low.")],
    )
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Set,
        value=1.0,
    )
    result = snap.apply_resource_patch(patch)

    assert len(result.crossed_thresholds) == 1
    assert result.crossed_thresholds[0].event_id == "luck_low"


# ===========================================================================
# AC6: decay_per_turn reduces current value each turn
# ===========================================================================


def test_resource_pool_decay_reduces_current() -> None:
    pool = ResourcePool(
        name="heat",
        label="Heat",
        current=5.0,
        min=0.0,
        max=10.0,
        voluntary=False,
        decay_per_turn=-0.5,
        thresholds=[],
    )
    snap = snapshot_with_pools([pool])

    snap.apply_pool_decay()

    assert snap.resources["heat"].current == pytest.approx(4.5)


def test_resource_pool_decay_clamps_to_min() -> None:
    pool = ResourcePool(
        name="heat",
        label="Heat",
        current=0.3,
        min=0.0,
        max=10.0,
        voluntary=False,
        decay_per_turn=-0.5,
        thresholds=[],
    )
    snap = snapshot_with_pools([pool])

    snap.apply_pool_decay()

    assert snap.resources["heat"].current == pytest.approx(0.0), (
        "decay should clamp to min, not go negative"
    )


def test_resource_pool_positive_decay_increases() -> None:
    pool = ResourcePool(
        name="mana",
        label="Mana",
        current=5.0,
        min=0.0,
        max=10.0,
        voluntary=True,
        decay_per_turn=1.0,
        thresholds=[],
    )
    snap = snapshot_with_pools([pool])

    snap.apply_pool_decay()

    assert snap.resources["mana"].current == pytest.approx(6.0)


def test_resource_pool_positive_decay_clamps_to_max() -> None:
    pool = ResourcePool(
        name="mana",
        label="Mana",
        current=9.5,
        min=0.0,
        max=10.0,
        voluntary=True,
        decay_per_turn=1.0,
        thresholds=[],
    )
    snap = snapshot_with_pools([pool])

    snap.apply_pool_decay()

    assert snap.resources["mana"].current == pytest.approx(10.0)


def test_resource_pool_zero_decay_no_change() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    snap.apply_pool_decay()

    assert snap.resources["luck"].current == pytest.approx(3.0)


# ===========================================================================
# AC7: voluntary flag controls whether player can spend
# ===========================================================================


def test_voluntary_resource_allows_player_spend() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    pool.voluntary = True
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    result = snap.apply_resource_patch_player(patch)

    assert isinstance(result, ResourcePatchResult)
    assert snap.resources["luck"].current == pytest.approx(2.0)


def test_involuntary_resource_rejects_player_spend() -> None:
    """Player-initiated subtract on ``voluntary=False`` must raise and
    leave state unchanged (atomicity + AC7)."""
    pool = make_pool("heat", 5.0, 0.0, 10.0)
    pool.voluntary = False
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="heat",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    with pytest.raises(ResourcePatchError):
        snap.apply_resource_patch_player(patch)

    assert snap.resources["heat"].current == pytest.approx(5.0), (
        "rejected player spend must not modify state"
    )


def test_involuntary_resource_allows_engine_modification() -> None:
    """Engine-level ``apply_resource_patch`` ignores the voluntary flag
    (only ``apply_resource_patch_player`` enforces it)."""
    pool = make_pool("heat", 5.0, 0.0, 10.0)
    pool.voluntary = False
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="heat",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    snap.apply_resource_patch(patch)

    assert snap.resources["heat"].current == pytest.approx(4.0)


def test_involuntary_resource_allows_add_from_player() -> None:
    """AC7 — ``voluntary`` only gates SUBTRACT; player can add freely."""
    pool = make_pool("heat", 5.0, 0.0, 10.0)
    pool.voluntary = False
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="heat",
        operation=ResourcePatchOp.Add,
        value=1.0,
    )
    result = snap.apply_resource_patch_player(patch)
    assert isinstance(result, ResourcePatchResult)


# ===========================================================================
# AC4: Genre pack declarations → pools via ``init_resource_pools``
# ===========================================================================


def test_init_pools_from_declarations() -> None:
    from sidequest.genre.models.rules import ResourceDeclaration

    snap = GameSnapshot()
    decl = ResourceDeclaration(
        name="luck",
        label="Luck",
        min=0.0,
        max=6.0,
        starting=3.0,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=[],
    )

    snap.init_resource_pools([decl])

    assert "luck" in snap.resources
    pool = snap.resources["luck"]
    assert pool.name == "luck"
    assert pool.current == pytest.approx(3.0), "current should equal declaration.starting"
    assert pool.min == pytest.approx(0.0)
    assert pool.max == pytest.approx(6.0)
    assert pool.voluntary is True


def test_init_pools_multiple_declarations() -> None:
    from sidequest.genre.models.rules import ResourceDeclaration

    snap = GameSnapshot()
    luck = ResourceDeclaration(
        name="luck",
        label="Luck",
        min=0.0,
        max=6.0,
        starting=3.0,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=[],
    )
    heat = ResourceDeclaration(
        name="heat",
        label="Heat",
        min=0.0,
        max=10.0,
        starting=0.0,
        voluntary=False,
        decay_per_turn=-0.1,
        thresholds=[],
    )

    snap.init_resource_pools([luck, heat])

    assert len(snap.resources) == 2
    assert "luck" in snap.resources
    assert "heat" in snap.resources
    assert snap.resources["heat"].voluntary is False
    assert snap.resources["heat"].decay_per_turn == pytest.approx(-0.1)


def test_init_pools_empty_declarations_no_crash() -> None:
    snap = GameSnapshot()
    snap.init_resource_pools([])
    assert snap.resources == {}


def test_init_pools_preserves_current_on_upsert() -> None:
    """Critical save-migration semantic: declaration upsert MUST NOT clobber
    a pool's existing ``current`` value — it updates label/min/max/voluntary/
    decay/thresholds only. Rust ``init_resource_pools`` comment spells this
    out verbatim."""
    from sidequest.genre.models.rules import ResourceDeclaration

    # Simulate a loaded save with current=2.0
    pool = make_pool("luck", 2.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    # Declaration says starting=5.0 — must NOT overwrite current.
    decl = ResourceDeclaration(
        name="luck",
        label="Luck",
        min=0.0,
        max=6.0,
        starting=5.0,
        voluntary=True,
        decay_per_turn=0.0,
        thresholds=[],
    )
    snap.init_resource_pools([decl])

    assert snap.resources["luck"].current == pytest.approx(2.0), (
        "init_resource_pools upsert must preserve existing current"
    )
    # But label should update from the declaration
    assert snap.resources["luck"].label == "Luck"


# ===========================================================================
# Edge cases and integration
# ===========================================================================


def test_resource_patch_result_contains_new_value() -> None:
    pool = make_pool("luck", 3.0, 0.0, 6.0)
    snap = snapshot_with_pools([pool])

    patch = ResourcePatch(
        resource_name="luck",
        operation=ResourcePatchOp.Subtract,
        value=1.0,
    )
    result = snap.apply_resource_patch(patch)

    assert result.new_value == pytest.approx(2.0)
    assert result.old_value == pytest.approx(3.0)


def test_decay_triggers_threshold_crossings() -> None:
    pool = make_pool_with_thresholds(
        "heat",
        1.0,
        0.0,
        10.0,
        [make_threshold(0.5, "heat_low", "Cooling down.")],
    )
    pool.decay_per_turn = -0.6
    snap = snapshot_with_pools([pool])

    crossings = snap.apply_pool_decay()

    # Value went from 1.0 to 0.4 — crossed threshold at 0.5
    assert len(crossings) >= 1
    assert crossings[0].event_id == "heat_low"


def test_multiple_pools_independent_patches() -> None:
    luck = make_pool("luck", 3.0, 0.0, 6.0)
    heat = make_pool("heat", 5.0, 0.0, 10.0)
    snap = snapshot_with_pools([luck, heat])

    snap.apply_resource_patch(
        ResourcePatch(resource_name="luck", operation=ResourcePatchOp.Subtract, value=1.0),
    )
    snap.apply_resource_patch(
        ResourcePatch(resource_name="heat", operation=ResourcePatchOp.Add, value=2.0),
    )

    assert snap.resources["luck"].current == pytest.approx(2.0)
    assert snap.resources["heat"].current == pytest.approx(7.0)


def test_resource_pool_with_thresholds_survives_snapshot_roundtrip() -> None:
    pool = make_pool_with_thresholds(
        "luck",
        3.0,
        0.0,
        6.0,
        [make_threshold(1.0, "luck_critical", "Nearly out.")],
    )
    snap = snapshot_with_pools([pool])

    restored = GameSnapshot.model_validate_json(snap.model_dump_json())
    restored_pool = restored.resources["luck"]
    assert len(restored_pool.thresholds) == 1
    assert restored_pool.thresholds[0].event_id == "luck_critical"
    assert restored_pool.thresholds[0].at == pytest.approx(1.0)


# ===========================================================================
# Story 16-11 threshold-lore minting tests live in
# tests/game/test_resource_threshold_lore.py (per architect pre-red
# reconciled AC6 — 1:1 file mapping with Rust source layout).
# ===========================================================================


# ===========================================================================
# Wiring tests — AC5 + AC6 + CLAUDE.md "Every Test Suite Needs a Wiring Test"
# ===========================================================================


def test_game_snapshot_resources_type_annotation_is_typed_resource_pool() -> None:
    """AC5 — ``GameSnapshot.resources`` must be typed ``dict[str, ResourcePool]``
    where ``ResourcePool`` is the real class from ``sidequest.game.resource_pool``,
    NOT the P4-deferred stub that used to live in ``session.py``.
    """
    import typing

    from sidequest.game.resource_pool import ResourcePool as RealResourcePool

    hints = typing.get_type_hints(GameSnapshot)
    resources_type = hints["resources"]
    # dict[str, ResourcePool] — args are (str, ResourcePool)
    args = typing.get_args(resources_type)
    assert args[0] is str, f"resources dict key must be str, got {args[0]}"
    assert args[1] is RealResourcePool, (
        f"resources dict value must be sidequest.game.resource_pool.ResourcePool, got {args[1]!r}"
    )


def test_resource_pool_single_source_of_truth() -> None:
    """No two ``ResourcePool`` classes — ``sidequest.game.ResourcePool`` must
    be the real one from ``sidequest.game.resource_pool``, not the session.py
    stub."""
    import sidequest.game as game
    from sidequest.game.resource_pool import ResourcePool as RealResourcePool

    assert game.ResourcePool is RealResourcePool, (
        "sidequest.game.ResourcePool must re-export the resource_pool.py class "
        "(single source of truth), not a duplicate in session.py"
    )


def test_sidequest_game_re_exports_resource_pool_symbols() -> None:
    """Downstream consumers (dispatch, narrator, GM panel) import via
    ``from sidequest.game import ...`` — every new 42-2 symbol must be
    re-exported from the package root. Per 42-1 binding rule, wiring
    tests cover every re-exported symbol plus every method-surface
    contract declared in the module docstring."""
    import sidequest.game as game
    from sidequest.game import GameSnapshot

    for sym in (
        "ResourcePool",
        "ResourceThreshold",
        "ResourcePatch",
        "ResourcePatchOp",
        "ResourcePatchResult",
        "ResourcePatchError",
        "UnknownResource",
        "NotVoluntary",
        "detect_crossings",
        "mint_threshold_lore",
    ):
        assert hasattr(game, sym), f"sidequest.game must re-export {sym!r}"

    # Method surface per the test-file's module docstring contract.
    for method_name in (
        "apply_resource_patch",
        "apply_resource_patch_player",
        "apply_pool_decay",
        "init_resource_pools",
        "apply_resource_patch_by_name",
        "process_resource_patch_with_lore",
    ):
        assert callable(getattr(GameSnapshot, method_name, None)), (
            f"GameSnapshot must expose {method_name!r} as a callable"
        )


def test_resource_pool_model_config_is_forbid() -> None:
    """Reviewer's 42-1 binding call: internal engine types use
    ``extra: forbid``. Only save-file surfaces (``GameSnapshot``) use
    ``ignore``. ``ResourcePool`` is engine-internal — must be ``forbid``
    so malformed pool dicts fail loud per CLAUDE.md / AC5."""
    from sidequest.game.resource_pool import (
        ResourcePatch,
        ResourcePool,
        ResourceThreshold,
    )

    for cls in (ResourcePool, ResourceThreshold, ResourcePatch):
        extra = cls.model_config.get("extra")
        assert extra == "forbid", (
            f"{cls.__name__} must use extra='forbid' per Reviewer's 42-1 binding call "
            f"(got {extra!r})"
        )


def test_malformed_pool_dict_fails_loud_on_snapshot_load() -> None:
    """AC5 — a save with an unknown field inside a pool dict must raise,
    not silently drop. Relies on ``ResourcePool`` being ``extra='forbid'``."""
    payload = {
        "resources": {
            "luck": {
                "name": "luck",
                "label": "Luck",
                "current": 3.0,
                "min": 0.0,
                "max": 6.0,
                "voluntary": True,
                "decay_per_turn": 0.0,
                "thresholds": [],
                "flibbertigibbet": "oh no",  # unknown field
            }
        }
    }

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GameSnapshot.model_validate(payload)


def test_resource_patch_error_subclasses_are_exceptions() -> None:
    """``ResourcePatchError`` must be a proper exception type so callers
    can ``pytest.raises`` / ``try/except`` on it."""
    from sidequest.game.resource_pool import ResourcePatchError

    assert issubclass(ResourcePatchError, Exception)
