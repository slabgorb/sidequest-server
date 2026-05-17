"""Tests for sidequest.dungeon.setpiece_attach — Plan 6, Tasks 1 & 2.

Task 1 checkboxes:
  1. identical inputs → byte-identical rolled result (frozen-into-save contract).
  2. distinct (region_id|setpiece_id|slot_id) tuples do not collude.
  3. a ComponentSlot with one option always picks that option; empty options
     list is rejected by Plan 4's validator — assert the guard still holds.

Task 2 checkboxes:
  1. A started trope appears in snap.active_tropes with status="progressing";
     a subsequent tick_tropes advances its progress (lie detector / wiring proof).
  2. Unknown trope_id raises loudly (content bug surfaced, not swallowed);
     the failure path still emits a trope.start span carrying the failure.
  3. threads_lit_per_expansion bounds the count — with budget N and components
     > N, at most N are lit, and the selection is deterministic from the seed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from sidequest.dungeon.setpiece_attach import (
    RolledSetPiece,
    _slot_seed,
    roll_set_piece,
    start_trope_components,
)
from sidequest.dungeon.setpieces import (
    ComponentSlot,
    SetPiece,
    TropeComponent,
)
from sidequest.game.session import GameSnapshot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_set_piece(slots: list[dict]) -> SetPiece:
    """Build a minimal valid SetPiece with given slot specs."""
    return SetPiece.model_validate(
        {
            "id": "test_trap",
            "name": "The Test Trap",
            "telegraph": "Pressure plate visible under grime.",
            "outcome": "Spears shoot from the walls.",
            "slots": slots,
        }
    )


_MULTI_OPTION_SET_PIECE = _make_set_piece(
    [
        {
            "name": "layout",
            "options": [
                {"value": "pit", "weight": 1.0},
                {"value": "corridor", "weight": 1.0},
                {"value": "chamber", "weight": 1.0},
            ],
        },
        {
            "name": "loot",
            "options": [
                {"value": "gold_coins", "weight": 2.0},
                {"value": "cursed_ring", "weight": 1.0},
            ],
        },
    ]
)

_SINGLE_OPTION_SET_PIECE = _make_set_piece(
    [
        {
            "name": "layout",
            "options": [{"value": "only_choice", "weight": 1.0}],
        }
    ]
)


# ---------------------------------------------------------------------------
# Test 1: identical inputs → byte-identical rolled result
# (frozen-into-save contract; spec §7)
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_identical_result():
    """Same call twice must produce an equal RolledSetPiece."""
    kwargs = dict(
        campaign_seed=999,
        expansion_id=1,
        region_id="exp001.r3",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    result_a = roll_set_piece(**kwargs)
    result_b = roll_set_piece(**kwargs)
    assert result_a == result_b


def test_determinism_against_hardcoded_expected_value():
    """Assert exact rolled values computed once; a seed-algorithm change
    must break this test loudly (frozen-into-save contract)."""
    result = roll_set_piece(
        campaign_seed=42,
        expansion_id=3,
        region_id="exp003.r7",
        setpiece_id="false_floor",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    # These values were computed by running the implementation once and are
    # now pinned. If the seed algorithm changes, this fails loudly — exactly
    # what "frozen into save" requires.
    assert isinstance(result, RolledSetPiece)
    assert set(result.slots.keys()) == {"layout", "loot"}
    # Pin the exact chosen values:
    assert result.slots["layout"] == "corridor"
    assert result.slots["loot"] == "gold_coins"


# ---------------------------------------------------------------------------
# Test 2: distinct (region_id|setpiece_id|slot_id) tuples do not collude
# ---------------------------------------------------------------------------


def test_distinct_region_ids_produce_different_rolls():
    """Different region_ids must not always roll the same slot values.

    Use a three-option slot with equal weights and a large sample of
    region_ids; expect non-constant results (probability of all-same is
    (1/3)^(N-1) ≈ negligible for N=20)."""
    results = set()
    for i in range(20):
        r = roll_set_piece(
            campaign_seed=1,
            expansion_id=1,
            region_id=f"exp001.r{i}",
            setpiece_id="same_piece",
            set_piece=_MULTI_OPTION_SET_PIECE,
        )
        results.add(r.slots["layout"])
    assert len(results) > 1, (
        "All 20 distinct region_ids rolled the same layout option — "
        "seed mixing is colliding across region_ids"
    )


def test_distinct_setpiece_ids_produce_different_rolls():
    """Different setpiece_ids with the same region must not always collude."""
    results = set()
    for i in range(20):
        r = roll_set_piece(
            campaign_seed=1,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id=f"piece_{i}",
            set_piece=_MULTI_OPTION_SET_PIECE,
        )
        results.add(r.slots["layout"])
    assert len(results) > 1, (
        "All 20 distinct setpiece_ids rolled the same layout option — "
        "seed mixing is colliding across setpiece_ids"
    )


def test_slot_id_collision_prevention():
    """Two different slots in the same set-piece must use independent RNGs.

    Use a set-piece with two slots having the same option distribution;
    if the per-slot sub-seed includes the slot name as the distinguisher,
    different slots should roll independently — not always identically."""
    # Build a set-piece where both slots have identical option lists.
    # If sub-seeding doesn't distinguish slots, they'd always match.
    symmetric = _make_set_piece(
        [
            {
                "name": "slot_alpha",
                "options": [
                    {"value": "A", "weight": 1.0},
                    {"value": "B", "weight": 1.0},
                    {"value": "C", "weight": 1.0},
                ],
            },
            {
                "name": "slot_beta",
                "options": [
                    {"value": "A", "weight": 1.0},
                    {"value": "B", "weight": 1.0},
                    {"value": "C", "weight": 1.0},
                ],
            },
        ]
    )
    same_count = 0
    for seed in range(30):
        r = roll_set_piece(
            campaign_seed=seed,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id="symmetric_piece",
            set_piece=symmetric,
        )
        if r.slots["slot_alpha"] == r.slots["slot_beta"]:
            same_count += 1
    # With 3 equal-weight options, expected same-fraction ≈ 1/3.
    # If sub-seeding doesn't separate slots, same_count would be 30.
    assert same_count < 30, (
        "slot_alpha and slot_beta always matched — slot sub-seeding "
        "is not distinguishing between slots within a set-piece"
    )


def test_prefix_collision_prevention():
    """campaign=1/expansion=23 and campaign=12/expansion=3 must produce
    distinct seeds with all other inputs identical — the pipe delimiter
    prevents naive concatenation aliasing ('1'+'23' == '12'+'3' == '123').

    Holds region_id/setpiece_id/slot_name constant so the ONLY difference
    is the (campaign_seed, expansion_id) split — a no-delimiter _slot_seed
    would alias these to the same seed and this test would fail loudly."""
    seed_1_23 = _slot_seed(
        campaign_seed=1,
        expansion_id=23,
        region_id="fixed.r0",
        setpiece_id="piece",
        slot_name="layout",
    )
    seed_12_3 = _slot_seed(
        campaign_seed=12,
        expansion_id=3,
        region_id="fixed.r0",
        setpiece_id="piece",
        slot_name="layout",
    )
    assert seed_1_23 != seed_12_3, (
        "(campaign=1,expansion=23) and (campaign=12,expansion=3) produce "
        "the same seed — delimiter is not preventing concatenation aliasing"
    )


# ---------------------------------------------------------------------------
# Test 3: single-option slot; Plan 4 guard still holds
# ---------------------------------------------------------------------------


def test_single_option_slot_always_picks_that_option():
    """A ComponentSlot with exactly one option must always roll that option."""
    for seed in range(10):
        result = roll_set_piece(
            campaign_seed=seed,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id="one_choice",
            set_piece=_SINGLE_OPTION_SET_PIECE,
        )
        assert result.slots["layout"] == "only_choice"


def test_plan4_validator_still_rejects_empty_options():
    """Plan 4's guard rejects empty options lists — assert it still holds."""
    with pytest.raises(ValidationError, match="at least one option"):
        ComponentSlot(name="layout", options=[])


# ---------------------------------------------------------------------------
# Test: RolledSetPiece shape
# ---------------------------------------------------------------------------


def test_rolled_setpiece_contains_all_slot_names():
    """RolledSetPiece.slots must have one entry per ComponentSlot."""
    result = roll_set_piece(
        campaign_seed=0,
        expansion_id=0,
        region_id="exp000.r0",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    assert isinstance(result, RolledSetPiece)
    assert set(result.slots.keys()) == {"layout", "loot"}
    # Each value must be a valid option value string
    layout_values = {o.value for o in _MULTI_OPTION_SET_PIECE.slots[0].options}
    loot_values = {o.value for o in _MULTI_OPTION_SET_PIECE.slots[1].options}
    assert result.slots["layout"] in layout_values
    assert result.slots["loot"] in loot_values


# ===========================================================================
# Task 2: Trope-component start → live trope engine (ADR-018 seam)
# ===========================================================================

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_trope_def(
    trope_id: str,
    rate_per_turn: float = 0.05,
) -> Any:
    """Minimal TropeDefinition-shaped object.

    tick_tropes duck-types on .id, .passive_progression.rate_per_turn,
    and .escalation (checked in _fire_one_staggered_beat Pass B).
    Using SimpleNamespace keeps the fixture free of the genre loader so the
    test does not require a real genre pack on disk.
    """
    passive = SimpleNamespace(
        rate_per_turn=rate_per_turn,
        rate_per_day=0.0,
        accelerators=[],
        decelerators=[],
        accelerator_bonus=0.0,
        decelerator_penalty=0.0,
    )
    return SimpleNamespace(
        id=trope_id,
        passive_progression=passive,
        escalation=[],  # no beats — tick advances progress without firing
    )


def _make_pack(*trope_defs: Any) -> Any:
    """Minimal pack-shaped object carrying a .tropes list."""
    return SimpleNamespace(tropes=list(trope_defs))


def _make_components(*trope_ids: str, params: dict | None = None) -> list[TropeComponent]:
    """Build TropeComponent list with optional shared params."""
    return [TropeComponent(trope_id=tid, params=params or {}) for tid in trope_ids]


def _fresh_snapshot() -> GameSnapshot:
    """Minimal GameSnapshot with empty active_tropes."""
    return GameSnapshot(genre_slug="caverns_and_claudes", world_slug="test_world")


# ---------------------------------------------------------------------------
# Task 2 Test 1: started trope appears in active_tropes with
# status="progressing" AND tick_tropes advances its progress (lie detector).
# ---------------------------------------------------------------------------


def test_started_trope_is_progressing_and_tick_advances_it() -> None:
    """Wiring proof: start_trope_components appends a live TropeState that
    tick_tropes actually advances — not an inert blob the engine ignores."""
    from sidequest.game.trope_tick import tick_tropes

    trope_def = _make_trope_def("cave_in", rate_per_turn=0.1)
    pack = _make_pack(trope_def)
    snapshot = _fresh_snapshot()
    components = _make_components("cave_in")

    result = start_trope_components(
        campaign_seed=42,
        expansion_id=1,
        region_id="exp001.r5",
        setpiece_id="collapse_hall",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
    )

    # 1a — TropeState appended with correct fields
    assert result.tropes_started == 1
    assert len(snapshot.active_tropes) == 1
    ts = snapshot.active_tropes[0]
    assert ts.id == "cave_in"
    assert ts.status == "progressing"
    assert ts.progress == 0.0

    # 1b — pending carries component + origin_region for Task 4
    assert len(result.pending) == 1
    comp, origin_region = result.pending[0]
    assert comp.trope_id == "cave_in"
    assert origin_region == "exp001.r5"

    # 1c — tick_tropes with a real pack advances progress (lie detector)
    progress_before = ts.progress
    tick_tropes(snapshot, pack, now_turn=1)
    assert snapshot.active_tropes[0].progress > progress_before, (
        "tick_tropes did not advance progress — trope is inert, not live"
    )


# ---------------------------------------------------------------------------
# Task 2 Test 2: unknown trope_id raises loudly; span carries failure.
# ---------------------------------------------------------------------------


def test_unknown_trope_id_raises_loudly() -> None:
    """An unresolvable trope_id must raise ValueError (content authoring bug),
    not silently skip. The span must be emitted even on failure so the GM
    panel sees the content bug."""
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")

    pack = _make_pack()  # empty — no tropes registered
    snapshot = _fresh_snapshot()
    components = _make_components("ghost_lights")

    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="ghost_lights"):
            start_trope_components(
                campaign_seed=7,
                expansion_id=2,
                region_id="exp002.r1",
                setpiece_id="haunted_alcove",
                components=components,
                pack_tropes=pack,
                snapshot=snapshot,
                threads_lit_per_expansion=10,
                threads_already_lit=0,
            )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # Span must have been emitted carrying the failure
    finished = exporter.get_finished_spans()
    trope_start_spans = [s for s in finished if s.name == "trope.start"]
    assert trope_start_spans, "trope.start span was NOT emitted on failure — GM panel is blind"
    span_attrs = trope_start_spans[0].attributes or {}
    assert span_attrs.get("trope_id") == "ghost_lights"
    assert span_attrs.get("failed") is True


# ---------------------------------------------------------------------------
# Task 2 Test 3: threads_lit_per_expansion bounds the count; selection is
# deterministic from the seed.
# ---------------------------------------------------------------------------


def test_budget_caps_tropes_lit() -> None:
    """With 4 trope components and budget=2 (remaining=2), exactly 2 are lit.
    Which 2 is deterministic from the seed — running twice gives the same pair."""
    trope_defs = [_make_trope_def(f"trope_{i}") for i in range(4)]
    pack = _make_pack(*trope_defs)
    components = _make_components("trope_0", "trope_1", "trope_2", "trope_3")

    # Run A
    snapshot_a = _fresh_snapshot()
    result_a = start_trope_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot_a,
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )

    # Run B — same inputs, must produce identical lit set
    snapshot_b = _fresh_snapshot()
    start_trope_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot_b,
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )

    # Budget enforced
    assert result_a.tropes_started == 2
    assert len(snapshot_a.active_tropes) == 2

    # Deterministic — same seed → same selection
    ids_a = {t.id for t in snapshot_a.active_tropes}
    ids_b = {t.id for t in snapshot_b.active_tropes}
    assert ids_a == ids_b, "selection is non-deterministic across identical inputs"


def test_budget_with_already_lit_accumulator() -> None:
    """threads_already_lit reduces remaining budget; when already_lit >= budget,
    no tropes are lit."""
    trope_def = _make_trope_def("ceiling_crack")
    pack = _make_pack(trope_def)
    snapshot = _fresh_snapshot()
    components = _make_components("ceiling_crack")

    result = start_trope_components(
        campaign_seed=5,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="full_budget",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot,
        threads_lit_per_expansion=1,
        threads_already_lit=1,  # budget exhausted
    )
    assert result.tropes_started == 0
    assert snapshot.active_tropes == []


def test_budget_no_silent_default() -> None:
    """threads_lit_per_expansion has no default — omitting it raises TypeError,
    not a silent fallback."""
    pack = _make_pack()
    snapshot = _fresh_snapshot()
    with pytest.raises(TypeError):
        start_trope_components(  # type: ignore[call-arg]
            campaign_seed=1,
            expansion_id=1,
            region_id="r0",
            setpiece_id="p0",
            components=[],
            pack_tropes=pack,
            snapshot=snapshot,
            threads_already_lit=0,
            # threads_lit_per_expansion intentionally omitted
        )
