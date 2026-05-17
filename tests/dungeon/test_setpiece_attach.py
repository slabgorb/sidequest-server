"""Tests for sidequest.dungeon.setpiece_attach — Plan 6, Tasks 1, 2 & 3.

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
     > N, at most N are lit, and the selection is deterministic from the seed
     AND frozen (hardcoded-pin canary — see
     test_trope_over_budget_selection_against_hardcoded_expected_value).

Task 3 checkboxes (reduced scope — see module docstring of
sidequest/dungeon/setpiece_attach.py and the plan's Post-Implementation
Corrections; ScenarioState SUPERSEDED, manifest-join reassigned to Plan 7):
  1. A seeded quest produces a real (QuestComponent, origin_region_id) pending
     entry that Task 4 can persist as ComplicationThread(kind="quest") via the
     REAL Plan 5 DungeonStore (lie detector — persist + read-back).
  2. No content-bug failure path BY DESIGN (no quest registry; manifest-join
     is Plan 7's). quest.seed is informational/success only — a fabricated
     failure test would be testing theater.
  3. threads_lit_per_expansion bounds the count, SHARED with Task 2's tropes;
     the over-budget selection is deterministic AND frozen (hardcoded-pin
     canary — see test_quest_over_budget_selection_against_hardcoded_expected_value).
  4. Duplicate quest_id in one set-piece seeds two pending entries (Task 4's
     thread_id collision reference — symmetric to the trope version).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from sidequest.dungeon.setpiece_attach import (
    QuestSeedResult,
    RolledSetPiece,
    _slot_seed,
    roll_set_piece,
    seed_quest_components,
    start_trope_components,
)
from sidequest.dungeon.setpieces import (
    ComponentSlot,
    QuestComponent,
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
    """Same call twice must produce an equal RolledSetPiece.

    Inputs are inlined into both call sites rather than splatted from a
    heterogeneous ``dict(...)`` literal: the dict literal infers a
    ``int | str | SetPiece`` value type and ``**kwargs`` loses the per-key
    arg types, tripping ~10 pyright reportArgumentType errors. Inlining
    keeps the signature types intact (Task 6 gate requires pyright-clean
    on this file)."""
    result_a = roll_set_piece(
        campaign_seed=999,
        expansion_id=1,
        region_id="exp001.r3",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    result_b = roll_set_piece(
        campaign_seed=999,
        expansion_id=1,
        region_id="exp001.r3",
        setpiece_id="test_trap",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
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
    # ignore: module attribute override (swap the tracer factory), not a
    # class/method override — pyright flags the reassignment shape only.
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
        # ignore: module attribute restore, not a method override (see above).
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


def test_trope_over_budget_selection_against_hardcoded_expected_value() -> None:
    """Frozen-into-save canary for trope OVER-BUDGET selection (the gap
    test_budget_caps_tropes_lit left open — it only proved within-process
    set-equality, which still passes if blake2b were swapped for md5 or the
    'trope_order|' prefix changed).

    A save written at campaign_seed=99 that lit {trope_1, trope_0} must light
    that SAME pair, in that SAME order, on every re-attach forever (spec §7
    save-is-truth; the rolled/selected set is frozen by Plan 7's commit and
    never recomputed). These values were computed once from the real
    _slot_seed implementation and are now PINNED. Any change to the sub-seed
    algorithm, the pipe delimiter, or the 'trope_order|<idx>' discriminator
    reorders the blake2b sort key and breaks this test loudly — exactly what
    the frozen-into-save contract requires. Mirrors Task 1's
    test_determinism_against_hardcoded_expected_value shape.

    Pinned full sort order at these inputs (lowest blake2b sub-seed first):
      trope_1 (10910839037820560710) < trope_0 (15753952436805574355)
      < trope_2 (15945093321800880024) < trope_3 (17643241989342562483)
    so budget=2 selects [trope_1, trope_0], appended in that order.
    """
    pack = _make_pack(*[_make_trope_def(f"trope_{i}") for i in range(4)])
    snapshot = _fresh_snapshot()
    components = _make_components("trope_0", "trope_1", "trope_2", "trope_3")

    result = start_trope_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot,
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )

    # EXACT pinned selection AND order — not just set membership.
    assert [t.id for t in snapshot.active_tropes] == ["trope_1", "trope_0"]
    assert [c.trope_id for c, _r in result.pending] == ["trope_1", "trope_0"]


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


def test_zero_budget_lights_nothing() -> None:
    """threads_lit_per_expansion=0 (with already_lit=0) lights nothing —
    distinct from the exhaustion case (budget=1, already_lit=1)."""
    trope_def = _make_trope_def("dripping_water")
    pack = _make_pack(trope_def)
    snapshot = _fresh_snapshot()
    components = _make_components("dripping_water")

    result = start_trope_components(
        campaign_seed=3,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="zero_budget",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot,
        threads_lit_per_expansion=0,
        threads_already_lit=0,
    )
    assert result.tropes_started == 0
    assert result.pending == []
    assert snapshot.active_tropes == []


# ---------------------------------------------------------------------------
# Task 2: atomicity — a bad trope_id rejects the whole set-piece's
# trope-start with ZERO snapshot mutation (no orphan TropeState on raise).
# ---------------------------------------------------------------------------


def test_unknown_trope_id_is_atomic_no_partial_mutation() -> None:
    """components=[known, unknown] with budget for both → ValueError raised
    AND snapshot.active_tropes still empty (no orphan TropeState) AND the
    trope.start failure span still emitted.

    This pins the validate-all-then-mutate ordering. Under the OLD one-pass
    ordering (open span → resolve → append, per component) the 'known'
    component would have been appended to snapshot.active_tropes BEFORE the
    'unknown' component's ValueError fired — leaving exactly one orphan
    TropeState in a live snapshot. This test asserts zero, so it fails
    against that old reasoning."""
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

    # Only "known_trope" is registered; "missing_trope" is the content bug.
    pack = _make_pack(_make_trope_def("known_trope"))
    snapshot = _fresh_snapshot()
    components = _make_components("known_trope", "missing_trope")

    original_tracer_fn = _spans_module.tracer
    # ignore: module attribute override (swap the tracer factory), not a
    # class/method override — pyright flags the reassignment shape only.
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        with pytest.raises(ValueError, match="missing_trope"):
            start_trope_components(
                campaign_seed=11,
                expansion_id=4,
                region_id="exp004.r9",
                setpiece_id="mixed_bag",
                components=components,
                pack_tropes=pack,
                snapshot=snapshot,
                threads_lit_per_expansion=10,  # budget allows BOTH
                threads_already_lit=0,
            )
    finally:
        # ignore: module attribute restore, not a method override (see above).
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # ATOMIC: zero snapshot mutation despite "known_trope" being resolvable
    # and within budget.
    assert snapshot.active_tropes == [], (
        "orphan TropeState left in snapshot — trope-start is not atomic"
    )

    # The failure span must still be emitted for the GM panel.
    finished = exporter.get_finished_spans()
    failure_spans = [
        s
        for s in finished
        if s.name == "trope.start" and (s.attributes or {}).get("failed") is True
    ]
    assert failure_spans, "trope.start failure span was NOT emitted — GM panel is blind"
    assert (failure_spans[0].attributes or {}).get("trope_id") == "missing_trope"


def test_duplicate_trope_id_in_one_setpiece_lights_two_states() -> None:
    """Pin current behavior: two identical TropeComponents (same trope_id) in
    one set-piece → two TropeState entries appended.

    This is intentional (each component lights its own thread). It is pinned
    so Task 4 sees the constraint: a thread_id derived from trope_id ALONE
    would collide here and trip Plan 5's open_thread duplicate-thread_id loud
    raise — Task 4 must use a per-component discriminator."""
    pack = _make_pack(_make_trope_def("twin_trap"))
    snapshot = _fresh_snapshot()
    components = _make_components("twin_trap", "twin_trap")

    result = start_trope_components(
        campaign_seed=8,
        expansion_id=2,
        region_id="exp002.r3",
        setpiece_id="double_trouble",
        components=components,
        pack_tropes=pack,
        snapshot=snapshot,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
    )

    assert result.tropes_started == 2
    assert len(snapshot.active_tropes) == 2
    assert [t.id for t in snapshot.active_tropes] == ["twin_trap", "twin_trap"]
    # Both pending entries carry the origin region for Task 4's ledger.
    assert len(result.pending) == 2
    assert all(region == "exp002.r3" for _comp, region in result.pending)


# ===========================================================================
# Task 3: Quest-component seed → pending ComplicationThread(kind="quest")
#
# REDUCED SCOPE (Architect decision 2026-05-16, logged in the plan's
# Post-Implementation Corrections): the "seed into ScenarioState" prose is
# SUPERSEDED — ScenarioState is a whodunit model and is NOT touched. A quest
# component is a future ComplicationThread(kind="quest") written via Plan 5's
# open_thread() by Task 4. There is NO quest registry to resolve quest_id
# against. The set-piece↔cookbook creature/loot manifest-join (the dropped
# Task-3 test 2) is REASSIGNED TO PLAN 7 (Plan 4 shipped no ref convention).
# Consequently reduced Task 3 has NO content-bug failure path — quest.seed is
# an informational/success span only. A fabricated failure test would be
# testing theater (the inverse of stubbing), so none is written by design.
# ===========================================================================


def _make_quest_components(*quest_ids: str, params: dict | None = None) -> list[QuestComponent]:
    """Build QuestComponent list with optional shared params."""
    return [QuestComponent(quest_id=qid, params=params or {}) for qid in quest_ids]


class _FakeManifest:
    """Duck-typed RegionContentManifest stand-in (.wandering_table /
    .loot_table). Plan 7 supplies the real RegionContentManifest at attach;
    reduced Task 3 accepts the parameter but does NOT resolve refs against it
    (manifest-join deferred to Plan 7 — see plan Post-Implementation
    Corrections). The fixture proves the parameter is accepted unchanged."""

    def __init__(self) -> None:
        self.wandering_table: list[dict] = [
            {"name": "Zombie", "cr": 0.25, "weight": 3, "count": "1d4"}
        ]
        self.loot_table: list[dict] = [
            {"name": "Grave Silver", "item_type": "treasure", "rarity": "common"}
        ]


class _PoisonManifest:
    """Manifest whose tables raise on ANY access — locks the
    "manifest is accepted but NEVER resolved against" contract for Tasks
    4-5 to inherit. If seed_quest_components ever iterates / indexes /
    truthiness-checks .wandering_table or .loot_table, this raises loudly
    (the manifest-join is Plan 7's by Architect decision — Plan 4 shipped
    no ref convention; see the setpiece_attach.py module docstring and the
    plan's Post-Implementation Corrections). A property (not an attribute)
    is used so even a bare attribute READ trips it — not just iteration."""

    @property
    def wandering_table(self) -> list[dict]:
        raise AssertionError(
            "seed_quest_components accessed manifest.wandering_table — "
            "Plan 7 owns creature/loot ref resolution; reduced Task 3 must "
            "NOT touch the manifest tables"
        )

    @property
    def loot_table(self) -> list[dict]:
        raise AssertionError(
            "seed_quest_components accessed manifest.loot_table — "
            "Plan 7 owns creature/loot ref resolution; reduced Task 3 must "
            "NOT touch the manifest tables"
        )


# ---------------------------------------------------------------------------
# Task 3 Test 1: a seeded quest produces a REAL pending entry that Task 4 can
# write as ComplicationThread(kind="quest") via DungeonStore.open_thread().
# This is the genuine lie-detector (symmetric to Task 2 Test 1) — it proves
# the pending shape is consumable end-to-end through the REAL Plan 5 ledger
# primitive, not just a struct write.
# ---------------------------------------------------------------------------


def test_seeded_quest_is_a_real_pending_ledger_thread() -> None:
    """seed_quest_components produces a (QuestComponent, origin_region_id)
    pending entry that Task 4 can turn into a ComplicationThread(kind="quest")
    and persist via Plan 5's real DungeonStore.open_thread() — then read back
    through the real DungeonStore.get_thread(). Wiring proof: the pending
    shape is end-to-end-consumable, not an inert blob."""
    import sqlite3  # noqa: PLC0415

    from sidequest.dungeon.persistence import (  # noqa: PLC0415
        ComplicationThread,
        DungeonStore,
    )

    components = _make_quest_components("deny_or_feed_the_altar", params={"irreversible": True})
    result = seed_quest_components(
        campaign_seed=42,
        expansion_id=1,
        region_id="exp001.r5",
        setpiece_id="the_altar_that_waits",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=10,
        threads_already_lit=0,
    )

    # The pending shape is symmetric with Task 2's TropeStartResult.pending.
    assert result.quests_seeded == 1
    assert len(result.pending) == 1
    comp, origin_region = result.pending[0]
    assert comp.quest_id == "deny_or_feed_the_altar"
    assert comp.params == {"irreversible": True}
    assert origin_region == "exp001.r5"

    # LIE DETECTOR: the pending entry must be consumable by the REAL Plan 5
    # ledger primitive. Build the ComplicationThread Task 4 will build and
    # persist+read it back through the real DungeonStore.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()
    thread = ComplicationThread(
        thread_id=f"quest|{origin_region}|0|{comp.quest_id}",
        origin_region_id=origin_region,
        kind="quest",
        status="open",
        started_at_depth_score=0.0,
        payload={"quest_id": comp.quest_id, "params": comp.params},
    )
    store.open_thread(thread)
    conn.commit()

    read_back = store.get_thread(thread.thread_id)
    assert read_back.kind == "quest"
    assert read_back.origin_region_id == "exp001.r5"
    assert read_back.status == "open"
    assert read_back.payload == {
        "quest_id": "deny_or_feed_the_altar",
        "params": {"irreversible": True},
    }


# ---------------------------------------------------------------------------
# Task 3 Test (test-3 reframed): no ScenarioState touched, no stub. The
# reconciliation killed the ScenarioState path; this asserts the honest
# equivalent — a REAL pending thread, and that seed_quest_components never
# imports or mutates ScenarioState.
# ---------------------------------------------------------------------------


def test_quest_seed_does_not_touch_scenario_state() -> None:
    """The ADR-053 supersession is real: seed_quest_components must NOT
    import, construct, or mutate ScenarioState. It produces only a real
    pending ComplicationThread-bound entry. This pins the reconciliation so
    a future change that re-introduces a ScenarioState dependency fails
    loudly here.

    The check is on real COUPLING (AST imports), not docstring prose — the
    module docstring legitimately *names* ScenarioState to document that the
    path is deliberately superseded. A substring scan would false-positive
    on that intentional documentation (and on this very test file)."""
    import ast  # noqa: PLC0415

    import sidequest.dungeon.setpiece_attach as mod  # noqa: PLC0415

    src = mod.__file__
    assert src is not None
    with open(src, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)

    imported_names: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
            for alias in node.names:
                imported_names.add(alias.name)

    assert "ScenarioState" not in imported_names, (
        "setpiece_attach.py imports ScenarioState — the Task-0 "
        "reconciliation (Seam 3) explicitly removed this coupling"
    )
    assert not any("scenario" in m for m in imported_modules), (
        "setpiece_attach.py imports a scenario module — forbidden by the "
        f"Task-0 reconciliation (imports: {sorted(imported_modules)})"
    )
    # Belt-and-braces: no ScenarioState symbol referenced anywhere in code
    # (ast.Name covers construction / attribute-base usage).
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "ScenarioState" not in referenced, (
        "setpiece_attach.py references the ScenarioState symbol in code — "
        "the reconciliation removed this path"
    )

    # And the produced pending entry is the real Task-4-consumable shape.
    result = seed_quest_components(
        campaign_seed=1,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="p0",
        components=_make_quest_components("a_quest"),
        manifest=_FakeManifest(),
        threads_lit_per_expansion=5,
        threads_already_lit=0,
    )
    assert isinstance(result, QuestSeedResult)
    assert result.quests_seeded == 1
    comp, region = result.pending[0]
    assert isinstance(comp, QuestComponent)
    assert region == "exp001.r0"


# ---------------------------------------------------------------------------
# Task 3 Test: budget bound + seed-determinism of over-budget selection
# (symmetric to Task 2's test_budget_caps_tropes_lit).
# ---------------------------------------------------------------------------


def test_quest_budget_caps_seeded_and_is_deterministic() -> None:
    """With 4 quest components and budget=2 (remaining=2), exactly 2 are
    seeded. Which 2 is deterministic from the seed via the shared _slot_seed
    family — running twice gives the same pair (no second seed scheme)."""
    components = _make_quest_components("quest_0", "quest_1", "quest_2", "quest_3")

    result_a = seed_quest_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )
    result_b = seed_quest_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )

    assert result_a.quests_seeded == 2
    assert len(result_a.pending) == 2
    ids_a = {c.quest_id for c, _r in result_a.pending}
    ids_b = {c.quest_id for c, _r in result_b.pending}
    assert ids_a == ids_b, "selection is non-deterministic across identical inputs"


def test_quest_over_budget_selection_against_hardcoded_expected_value() -> None:
    """Frozen-into-save canary for quest OVER-BUDGET selection (the gap
    test_quest_budget_caps_seeded_and_is_deterministic left open — it only
    proved within-process set-equality, which still passes if blake2b were
    swapped for md5 or the 'quest_order|' prefix changed).

    A save written at campaign_seed=99 that seeded {quest_1, quest_3} must
    seed that SAME pair, in that SAME order, on every re-attach forever
    (spec §7 save-is-truth; Plan 7's commit freezes the selected set and it
    is never recomputed). These values were computed once from the real
    _slot_seed implementation and are now PINNED. Any change to the sub-seed
    algorithm, the pipe delimiter, or the 'quest_order|<idx>' discriminator
    reorders the blake2b sort key and breaks this test loudly — exactly what
    the frozen-into-save contract requires. Mirrors Task 1's
    test_determinism_against_hardcoded_expected_value and the symmetric
    test_trope_over_budget_selection_against_hardcoded_expected_value.

    Pinned full sort order at these inputs (lowest blake2b sub-seed first):
      quest_1 (4998445612204798045) < quest_3 (11471368094519798342)
      < quest_2 (12272679721357361849) < quest_0 (17210404864106356272)
    so budget=2 selects [quest_1, quest_3], in that order.

    The 'quest_order|' prefix differs from trope's 'trope_order|', so quest
    selection at the SAME (seed, region, setpiece) does NOT mirror trope
    selection — pinning both proves the prefixes keep the two sub-streams
    independent (trope pins [trope_1, trope_0]; quest pins [quest_1, quest_3]).
    """
    components = _make_quest_components("quest_0", "quest_1", "quest_2", "quest_3")

    result = seed_quest_components(
        campaign_seed=99,
        expansion_id=3,
        region_id="exp003.r2",
        setpiece_id="budget_test",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=2,
        threads_already_lit=0,
    )

    # EXACT pinned selection AND order — not just set membership.
    assert [c.quest_id for c, _r in result.pending] == ["quest_1", "quest_3"]
    assert all(region == "exp003.r2" for _c, region in result.pending)


def test_duplicate_quest_id_in_one_setpiece_seeds_two_pending() -> None:
    """Pin current behavior: two identical QuestComponents (same quest_id) in
    one set-piece → two pending entries appended (Task 4's thread_id collision
    reference — symmetric to test_duplicate_trope_id_in_one_setpiece_lights_two_states).

    This is intentional (each component seeds its own thread). It is pinned
    so Task 4 sees the constraint: a thread_id derived from quest_id ALONE
    would collide here and trip Plan 5's open_thread duplicate-thread_id loud
    raise — Task 4 must use a per-component discriminator (origin region +
    component index / params)."""
    snapshot_region = "exp002.r3"
    components = _make_quest_components("twin_quest", "twin_quest")

    result = seed_quest_components(
        campaign_seed=8,
        expansion_id=2,
        region_id=snapshot_region,
        setpiece_id="double_trouble",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=10,
        threads_already_lit=0,
    )

    assert result.quests_seeded == 2
    assert len(result.pending) == 2
    assert [c.quest_id for c, _r in result.pending] == ["twin_quest", "twin_quest"]
    # Both pending entries carry the origin region for Task 4's ledger.
    assert all(region == snapshot_region for _c, region in result.pending)


def test_quest_shared_budget_accumulator_with_tropes() -> None:
    """threads_already_lit threads the SHARED expansion budget: Task 4 passes
    threads_already_lit = trope_result.tropes_started so quests consume what
    remains after tropes. With budget=3 and 2 already lit by tropes, only 1
    quest seeds even though 3 are offered."""
    components = _make_quest_components("q0", "q1", "q2")
    result = seed_quest_components(
        campaign_seed=7,
        expansion_id=2,
        region_id="exp002.r1",
        setpiece_id="shared_budget",
        components=components,
        manifest=_FakeManifest(),
        threads_lit_per_expansion=3,
        threads_already_lit=2,  # 2 tropes already consumed the budget
    )
    assert result.quests_seeded == 1
    assert len(result.pending) == 1


def test_quest_zero_budget_seeds_nothing() -> None:
    """threads_lit_per_expansion=0 (already_lit=0) seeds nothing — distinct
    from the exhaustion case."""
    result = seed_quest_components(
        campaign_seed=3,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="zero_budget",
        components=_make_quest_components("q0"),
        manifest=_FakeManifest(),
        threads_lit_per_expansion=0,
        threads_already_lit=0,
    )
    assert result.quests_seeded == 0
    assert result.pending == []


def test_quest_budget_exhausted_seeds_nothing() -> None:
    """already_lit >= budget → nothing seeds (budget fully consumed by
    tropes upstream)."""
    result = seed_quest_components(
        campaign_seed=5,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="full_budget",
        components=_make_quest_components("q0"),
        manifest=_FakeManifest(),
        threads_lit_per_expansion=1,
        threads_already_lit=1,
    )
    assert result.quests_seeded == 0
    assert result.pending == []


def test_quest_budget_no_silent_default() -> None:
    """threads_lit_per_expansion has no default — omitting it raises
    TypeError, not a silent fallback (symmetric to Task 2)."""
    with pytest.raises(TypeError):
        seed_quest_components(  # type: ignore[call-arg]
            campaign_seed=1,
            expansion_id=1,
            region_id="r0",
            setpiece_id="p0",
            components=[],
            manifest=_FakeManifest(),
            threads_already_lit=0,
            # threads_lit_per_expansion intentionally omitted
        )


# ---------------------------------------------------------------------------
# Task 3 Test: quest.seed span emitted per seeded component AND routed.
# Informational/success span (NOT a failure path — by design; the manifest
# join that would surface a content bug is Plan 7's, see module docstring).
# ---------------------------------------------------------------------------


def test_quest_seed_span_emitted_per_component_and_routed() -> None:
    """seed_quest_components emits one quest.seed span per seeded component,
    carrying quest_id / setpiece_id / origin_region_id. The span must also
    be registered in SPAN_ROUTES (routing-completeness contract)."""
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415
    from sidequest.telemetry.spans import SPAN_ROUTES  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_setpiece import (  # noqa: PLC0415
        SPAN_QUEST_SEED,
    )

    # Routing-completeness: the new constant must have a routing decision.
    assert SPAN_QUEST_SEED in SPAN_ROUTES, (
        "quest.seed has no SPAN_ROUTES entry — GM panel would miss it"
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")

    components = _make_quest_components("q_alpha", "q_beta")

    original_tracer_fn = _spans_module.tracer
    # ignore: module attribute override (swap the tracer factory), not a
    # class/method override — pyright flags the reassignment shape only.
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        result = seed_quest_components(
            campaign_seed=11,
            expansion_id=4,
            region_id="exp004.r9",
            setpiece_id="span_test",
            components=components,
            manifest=_FakeManifest(),
            threads_lit_per_expansion=10,
            threads_already_lit=0,
        )
    finally:
        # ignore: module attribute restore, not a method override (see above).
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    assert result.quests_seeded == 2
    finished = exporter.get_finished_spans()
    quest_seed_spans = [s for s in finished if s.name == "quest.seed"]
    assert len(quest_seed_spans) == 2, (
        "expected one quest.seed span per seeded component — GM panel needs the per-quest trail"
    )
    quest_ids = {(s.attributes or {}).get("quest_id") for s in quest_seed_spans}
    assert quest_ids == {"q_alpha", "q_beta"}
    for s in quest_seed_spans:
        attrs = s.attributes or {}
        assert attrs.get("setpiece_id") == "span_test"
        assert attrs.get("origin_region_id") == "exp004.r9"


def test_quest_seed_manifest_parameter_accepted_but_never_resolved_against() -> None:
    """The manifest parameter is REQUIRED (Plan 7 supplies the real
    RegionContentManifest so its call shape is ready) but reduced Task 3
    does NOT resolve refs against it — that join is Plan 7's (Plan 4 shipped
    no ref convention; see the setpiece_attach.py module docstring and the
    plan's Post-Implementation Corrections).

    Locked with a POISON manifest whose .wandering_table / .loot_table raise
    on ANY access. An empty-table sentinel would NOT prove non-iteration
    (an empty list passes whether or not it is iterated); the poison
    manifest fails loudly the instant the implementation so much as reads a
    table attribute. Even a params key that *looks* like a creature ref
    ({"creatures": ["Nonexistent"]}) must NOT trigger a manifest touch —
    ref-resolution is Plan 7's job by Architect decision. This locks the
    contract for Tasks 4-5 to inherit."""
    result = seed_quest_components(
        campaign_seed=2,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="no_resolution",
        components=_make_quest_components("q0", params={"creatures": ["Nonexistent"]}),
        manifest=_PoisonManifest(),
        threads_lit_per_expansion=5,
        threads_already_lit=0,
    )
    # Reaching here at all proves the poison properties were never accessed:
    # seed_quest_components did not iterate / index / truthiness-check the
    # manifest tables. ref-resolution is Plan 7's (Architect decision).
    assert result.quests_seeded == 1


# ===========================================================================
# Task 4: Ledger add — every started thread persisted (Plan 5 seam)
#
# Three checkbox tests:
#   1. N started threads → N ledger entries, each with origin region + open
#      status; the count equals trope.start + quest.seed spans (lie detector).
#   2. ledger add is part of the materialization transaction — if Plan 7's
#      commit aborts, no orphan ledger rows (caller-owns-txn contract).
#   3. AttachReport.as_dict() is a byte-pinned span contract — key-set locked
#      so Plan 7's attach span and the GM panel stay stable.
# ===========================================================================


def _otel_in_memory() -> tuple[Any, Any, Any]:
    """Return (exporter, provider, real_tracer) for in-memory OTEL tests."""
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")
    return exporter, provider, real_tracer


def _store_with_schema() -> tuple[Any, Any]:
    """Return (conn, store) with schema applied, using :memory: SQLite."""
    import sqlite3  # noqa: PLC0415

    from sidequest.dungeon.persistence import DungeonStore  # noqa: PLC0415

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()
    return conn, store


# ---------------------------------------------------------------------------
# Task 4 Checkbox 1: N started threads → N ledger entries, each with origin
# region + open status; the count equals trope.start + quest.seed spans.
# (lie detector cross-check)
# ---------------------------------------------------------------------------


def test_attach_set_piece_n_threads_produce_n_ledger_entries_lie_detector() -> None:
    """attach_set_piece with 2 tropes + 1 quest (budget=10) produces 3 open
    ledger entries, one per pending thread. The count of persisted open rows
    matches the count of trope.start + quest.seed spans emitted (lie detector
    — the cross-check guards against span theater where spans are emitted but
    no ledger rows are written, or vice versa)."""
    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_setpiece import (  # noqa: PLC0415
        SPAN_QUEST_SEED,
        SPAN_TROPE_START,
    )

    exporter, _provider, real_tracer = _otel_in_memory()
    conn, store = _store_with_schema()

    pack = _make_pack(
        _make_trope_def("cave_in"),
        _make_trope_def("dripping_water"),
    )
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])
    trope_components = _make_components("cave_in", "dripping_water")
    quest_components = _make_quest_components("deny_the_altar")

    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        report = attach_set_piece(
            campaign_seed=42,
            expansion_id=1,
            region_id="exp001.r5",
            setpiece_id="the_altar",
            set_piece=set_piece,
            trope_components=trope_components,
            quest_components=quest_components,
            pack_tropes=pack,
            snapshot=snapshot,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            started_at_depth_score=25.0,
        )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # Ledger: 3 open threads (2 tropes + 1 quest)
    open_threads = store.open_threads()
    assert len(open_threads) == 3, f"expected 3 open ledger entries, got {len(open_threads)}"
    for thread in open_threads:
        assert thread.status == "open"
        assert thread.origin_region_id == "exp001.r5"
        assert thread.started_at_depth_score == 25.0
        assert thread.kind in ("trope", "quest")

    # LIE DETECTOR: count must equal trope.start + quest.seed spans
    finished = exporter.get_finished_spans()
    trope_spans = [s for s in finished if s.name == SPAN_TROPE_START]
    quest_spans = [s for s in finished if s.name == SPAN_QUEST_SEED]
    span_total = len(trope_spans) + len(quest_spans)
    # trope.start emits per started component; quest.seed emits per seeded.
    # Lie detector: ledger rows == span count (not span theater).
    assert len(open_threads) == span_total, (
        f"ledger entries ({len(open_threads)}) != trope.start+quest.seed spans "
        f"({span_total}); possible span theater"
    )

    # setpiece.attach span emitted exactly once
    from sidequest.telemetry.spans.dungeon_setpiece import (  # noqa: PLC0415
        SPAN_SETPIECE_ATTACH,
    )

    attach_spans = [s for s in finished if s.name == SPAN_SETPIECE_ATTACH]
    assert len(attach_spans) == 1, (
        f"expected exactly one setpiece.attach span, got {len(attach_spans)}"
    )

    # Report matches persisted counts
    assert report.tropes_started == 2
    assert report.quests_seeded == 1
    assert report.threads_written == 3
    assert report.setpiece_id == "the_altar"
    assert report.region_id == "exp001.r5"


# ---------------------------------------------------------------------------
# Task 4 Checkbox 2: ledger add is part of the materialization transaction —
# if Plan 7's commit aborts, no orphan ledger rows (caller-owns-txn).
# ---------------------------------------------------------------------------


def test_attach_set_piece_no_orphan_rows_on_caller_rollback() -> None:
    """Simulates Plan 7 aborting its transaction after attach_set_piece runs.
    conn.rollback() must leave zero open ledger rows — attach_set_piece does
    NOT commit (caller owns the transaction boundary, spec §7.5)."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    conn, store = _store_with_schema()

    pack = _make_pack(_make_trope_def("ghost_light"))
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece(
        [{"name": "layout", "options": [{"value": "corridor", "weight": 1.0}]}]
    )

    attach_set_piece(
        campaign_seed=7,
        expansion_id=2,
        region_id="exp002.r1",
        setpiece_id="haunted_alcove",
        set_piece=set_piece,
        trope_components=_make_components("ghost_light"),
        quest_components=[],
        pack_tropes=pack,
        snapshot=snapshot,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=10.0,
    )

    # At this point (before commit) we should see the pending row in-txn
    in_txn_rows = store.open_threads()
    assert len(in_txn_rows) == 1, "expected 1 in-transaction ledger row before rollback"

    # Simulate Plan 7 aborting — rollback the whole transaction
    conn.rollback()

    # After rollback: zero rows (no orphan state)
    post_rollback_rows = store.open_threads()
    assert post_rollback_rows == [], (
        f"orphan ledger rows after rollback: {post_rollback_rows}; "
        "attach_set_piece must not autocommit (caller owns the txn boundary)"
    )


# ---------------------------------------------------------------------------
# Task 4 Checkbox 3: AttachReport.as_dict() byte-pinned — key-set locked.
# ---------------------------------------------------------------------------


def test_attach_report_as_dict_key_set_locked() -> None:
    """AttachReport.as_dict() must return EXACTLY the locked key set —
    neither more nor fewer keys. Adding or removing a field silently breaks
    Plan 7's attach span and the GM panel. The key set is locked here so
    any drift breaks this test loudly (mirrors DepthReport.as_dict() and
    GenerationReport.as_dict() shape contracts)."""
    from sidequest.dungeon.setpiece_attach import AttachReport  # noqa: PLC0415

    LOCKED_KEYS = {"setpiece_id", "region_id", "tropes_started", "quests_seeded", "threads_written"}

    report = AttachReport(
        setpiece_id="test_piece",
        region_id="exp001.r0",
        tropes_started=2,
        quests_seeded=1,
        threads_written=3,
    )
    d = report.as_dict()
    assert set(d.keys()) == LOCKED_KEYS, (
        f"AttachReport.as_dict() key set drifted from locked contract "
        f"{LOCKED_KEYS}; got {set(d.keys())}"
    )
    # Byte-pin exact values for fixed inputs
    assert d["setpiece_id"] == "test_piece"
    assert d["region_id"] == "exp001.r0"
    assert d["tropes_started"] == 2
    assert d["quests_seeded"] == 1
    assert d["threads_written"] == 3


def test_attach_report_as_dict_is_the_scalar_fields_minus_rolled() -> None:
    """as_dict() returns EXACTLY the flat SCALAR dataclass fields — every
    field EXCEPT ``rolled`` (spec §7.1 'fully legible, no hidden counters'
    for the OTEL span; ``rolled`` is a nested RolledSetPiece structure that
    is deliberately NOT a flat span attribute — it is Plan 7's freeze target,
    read off ``report.rolled`` directly). This pins the two-surface design:
    every scalar field IS in as_dict(); the one structured field is NOT."""
    import dataclasses  # noqa: PLC0415

    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        AttachReport,
        RolledSetPiece,
    )

    report = AttachReport(
        setpiece_id="sp1",
        region_id="r1",
        tropes_started=0,
        quests_seeded=0,
        threads_written=0,
        rolled=RolledSetPiece(slots={"layout": "pit"}),
    )
    all_field_names = {f.name for f in dataclasses.fields(report)}
    scalar_field_names = all_field_names - {"rolled"}

    assert set(report.as_dict().keys()) == scalar_field_names, (
        "as_dict() keys must equal the scalar dataclass fields (all fields except 'rolled')"
    )
    # rolled is a real dataclass field but intentionally absent from the
    # locked flat span contract.
    assert "rolled" in all_field_names, (
        "rolled must be a real AttachReport dataclass field (spec §7 freeze target Plan 7 reads)"
    )
    assert "rolled" not in report.as_dict(), (
        "rolled must NOT pollute the locked flat OTEL span contract"
    )


# ---------------------------------------------------------------------------
# Task 4: Additional coverage tests
# ---------------------------------------------------------------------------


def test_attach_set_piece_thread_ids_are_deterministic() -> None:
    """attach_set_piece produces the same thread_ids on repeated calls with
    identical inputs — frozen-into-save contract (Decision H)."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("ceiling_crack"))
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])

    conn_a, store_a = _store_with_schema()
    snap_a = _fresh_snapshot()
    attach_set_piece(
        campaign_seed=55,
        expansion_id=3,
        region_id="exp003.r1",
        setpiece_id="collapse",
        set_piece=set_piece,
        trope_components=_make_components("ceiling_crack"),
        quest_components=_make_quest_components("find_the_exit"),
        pack_tropes=pack,
        snapshot=snap_a,
        manifest=_FakeManifest(),
        store=store_a,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=15.0,
    )
    conn_a.commit()
    ids_a = {t.thread_id for t in store_a.open_threads()}

    conn_b, store_b = _store_with_schema()
    snap_b = _fresh_snapshot()
    attach_set_piece(
        campaign_seed=55,
        expansion_id=3,
        region_id="exp003.r1",
        setpiece_id="collapse",
        set_piece=set_piece,
        trope_components=_make_components("ceiling_crack"),
        quest_components=_make_quest_components("find_the_exit"),
        pack_tropes=pack,
        snapshot=snap_b,
        manifest=_FakeManifest(),
        store=store_b,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=15.0,
    )
    conn_b.commit()
    ids_b = {t.thread_id for t in store_b.open_threads()}

    assert ids_a == ids_b, (
        "thread_ids are non-deterministic across identical inputs — "
        "frozen-into-save contract violated (Decision H)"
    )


def test_attach_set_piece_duplicate_trope_id_produces_distinct_thread_ids() -> None:
    """Two TropeComponents with the same trope_id in one set-piece must produce
    two DISTINCT thread_ids — Decision H (per-component discriminator prevents
    collision with Plan 5's open_thread duplicate-thread_id loud raise)."""
    from sidequest.dungeon.persistence import PersistError  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("twin_trap"))
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])
    conn, store = _store_with_schema()
    snapshot = _fresh_snapshot()

    # Should NOT raise PersistError — two distinct thread_ids for same trope_id
    try:
        report = attach_set_piece(
            campaign_seed=8,
            expansion_id=2,
            region_id="exp002.r3",
            setpiece_id="double_trouble",
            set_piece=set_piece,
            trope_components=_make_components("twin_trap", "twin_trap"),
            quest_components=[],
            pack_tropes=pack,
            snapshot=snapshot,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            started_at_depth_score=5.0,
        )
    except PersistError as exc:
        raise AssertionError(
            f"Duplicate trope_id caused thread_id collision: {exc}; "
            "Decision H requires a per-component discriminator (component_index)"
        ) from exc

    open_threads = store.open_threads()
    assert len(open_threads) == 2
    thread_ids = [t.thread_id for t in open_threads]
    assert len(set(thread_ids)) == 2, (
        "Both threads have the same thread_id — per-component discriminator missing"
    )
    assert report.tropes_started == 2
    assert report.threads_written == 2


def test_attach_set_piece_tropes_consume_budget_before_quests() -> None:
    """Tropes consume the shared expansion budget first; quests get the
    remainder. With budget=2, 2 tropes started, 0 quests seeded (Decision C)."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("t0"), _make_trope_def("t1"))
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])
    conn, store = _store_with_schema()
    snapshot = _fresh_snapshot()

    report = attach_set_piece(
        campaign_seed=10,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="budget_shared",
        set_piece=set_piece,
        trope_components=_make_components("t0", "t1"),
        quest_components=_make_quest_components("q0", "q1"),
        pack_tropes=pack,
        snapshot=snapshot,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=2,
        threads_already_lit=0,
        started_at_depth_score=20.0,
    )

    assert report.tropes_started == 2
    assert report.quests_seeded == 0
    assert report.threads_written == 2

    open_threads = store.open_threads()
    assert len(open_threads) == 2
    assert all(t.kind == "trope" for t in open_threads)


def test_attach_set_piece_payload_is_legible() -> None:
    """ComplicationThread.payload carries setpiece_id, component_index,
    ref_id (trope_id or quest_id), and params — spec §7.1 'fully legible,
    no hidden counters' (Decision L)."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("poison_spores"))
    set_piece = _make_set_piece(
        [{"name": "layout", "options": [{"value": "corridor", "weight": 1.0}]}]
    )
    conn, store = _store_with_schema()
    snapshot = _fresh_snapshot()
    trope_comps = [TropeComponent(trope_id="poison_spores", params={"dmg": 2})]

    attach_set_piece(
        campaign_seed=1,
        expansion_id=1,
        region_id="exp001.r1",
        setpiece_id="the_cloud",
        set_piece=set_piece,
        trope_components=trope_comps,
        quest_components=[],
        pack_tropes=pack,
        snapshot=snapshot,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=8.0,
    )

    threads = store.open_threads()
    assert len(threads) == 1
    payload = threads[0].payload
    assert "setpiece_id" in payload and payload["setpiece_id"] == "the_cloud"
    assert "component_index" in payload
    assert "ref_id" in payload and payload["ref_id"] == "poison_spores"
    assert "params" in payload and payload["params"] == {"dmg": 2}


def test_attach_set_piece_started_at_depth_score_required_no_default() -> None:
    """started_at_depth_score has no default — omitting it raises TypeError
    (No Silent Fallbacks, Decision I)."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    _conn, store = _store_with_schema()
    pack = _make_pack()
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])

    with pytest.raises(TypeError):
        attach_set_piece(  # type: ignore[call-arg]
            campaign_seed=1,
            expansion_id=1,
            region_id="r0",
            setpiece_id="p0",
            set_piece=set_piece,
            trope_components=[],
            quest_components=[],
            pack_tropes=pack,
            snapshot=snapshot,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            # started_at_depth_score intentionally omitted
        )


def test_attach_set_piece_setpiece_attach_span_routed() -> None:
    """setpiece.attach span must be in SPAN_ROUTES — routing-completeness
    contract (mirrors the quest.seed and trope.start routing tests)."""
    from sidequest.telemetry.spans import SPAN_ROUTES  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_setpiece import (  # noqa: PLC0415
        SPAN_SETPIECE_ATTACH,
    )

    assert SPAN_SETPIECE_ATTACH in SPAN_ROUTES, (
        "setpiece.attach has no SPAN_ROUTES entry — GM panel would miss it"
    )


def test_attach_set_piece_re_attach_raises_persist_error() -> None:
    """Re-attach with identical inputs on the same store raises Plan 5's
    PersistError (duplicate thread_id = the spec §7 freeze-violation signal).
    NOT swallowed. Decision J: caller owns the txn; re-attach is the caller's
    mistake and the loud raise is the correct signal — and it raises on the
    FIRST open_thread (trope index 0) BEFORE any new rows land, so there is
    no partial-write problem (no validate-all-first pass needed)."""
    from sidequest.dungeon.persistence import PersistError  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("cave_in"))
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])
    conn, store = _store_with_schema()

    # First attach succeeds and is committed (the save is now truth, spec §7).
    snap_a = _fresh_snapshot()
    attach_set_piece(
        campaign_seed=99,
        expansion_id=1,
        region_id="exp001.r0",
        setpiece_id="frozen_piece",
        set_piece=set_piece,
        trope_components=_make_components("cave_in"),
        quest_components=_make_quest_components("escape"),
        pack_tropes=pack,
        snapshot=snap_a,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=12.0,
    )
    conn.commit()
    rows_before_reattach = len(store.open_threads())
    assert rows_before_reattach == 2  # 1 trope + 1 quest

    # Re-attach with IDENTICAL inputs — Plan 5's open_thread raises
    # PersistError on the duplicate thread_id. Specifically PersistError
    # (the freeze-violation signal), not a generic Exception.
    snap_b = _fresh_snapshot()
    with pytest.raises(PersistError):
        attach_set_piece(
            campaign_seed=99,
            expansion_id=1,
            region_id="exp001.r0",
            setpiece_id="frozen_piece",
            set_piece=set_piece,
            trope_components=_make_components("cave_in"),
            quest_components=_make_quest_components("escape"),
            pack_tropes=pack,
            snapshot=snap_b,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            started_at_depth_score=12.0,
        )

    # The raise landed on the FIRST duplicate (trope index 0) before any new
    # rows could be inserted — no partial write. Roll back the caller's txn
    # (Decision J: caller owns it) and confirm the original committed rows
    # are still the only rows.
    conn.rollback()
    assert len(store.open_threads()) == rows_before_reattach, (
        "re-attach left partial rows — open_thread must raise on the first "
        "duplicate before any new insert lands (Decision J)"
    )


def test_attach_report_rolled_is_the_deterministic_rolled_set_piece() -> None:
    """AttachReport.rolled carries the deterministic RolledSetPiece for the
    inputs (spec §7 freeze target Plan 7 persists and never recomputes), and
    `rolled` is NOT in as_dict() (the locked flat span contract stays
    unpolluted). Uses the exact Task 1 determinism expectations:
    test_determinism_against_hardcoded_expected_value pinned
    campaign_seed=42, expansion_id=3, region_id="exp003.r7",
    setpiece_id="false_floor" over _MULTI_OPTION_SET_PIECE →
    {layout: corridor, loot: gold_coins}."""
    from sidequest.dungeon.setpiece_attach import attach_set_piece  # noqa: PLC0415

    pack = _make_pack(_make_trope_def("cave_in"))
    conn, store = _store_with_schema()
    snapshot = _fresh_snapshot()

    report = attach_set_piece(
        campaign_seed=42,
        expansion_id=3,
        region_id="exp003.r7",
        setpiece_id="false_floor",
        set_piece=_MULTI_OPTION_SET_PIECE,
        trope_components=_make_components("cave_in"),
        quest_components=[],
        pack_tropes=pack,
        snapshot=snapshot,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=30.0,
    )

    # report.rolled is the deterministic RolledSetPiece — same pinned values
    # as Task 1's test_determinism_against_hardcoded_expected_value.
    assert isinstance(report.rolled, RolledSetPiece)
    assert set(report.rolled.slots.keys()) == {"layout", "loot"}
    assert report.rolled.slots["layout"] == "corridor"
    assert report.rolled.slots["loot"] == "gold_coins"

    # It must equal a direct roll_set_piece() with the same inputs (the
    # value Plan 7 freezes into the save, spec §7).
    direct = roll_set_piece(
        campaign_seed=42,
        expansion_id=3,
        region_id="exp003.r7",
        setpiece_id="false_floor",
        set_piece=_MULTI_OPTION_SET_PIECE,
    )
    assert report.rolled == direct

    # And it must NOT leak into the locked flat span contract.
    assert "rolled" not in report.as_dict(), (
        "rolled polluted the locked setpiece.attach span contract"
    )
    assert set(report.as_dict().keys()) == {
        "setpiece_id",
        "region_id",
        "tropes_started",
        "quests_seeded",
        "threads_written",
    }


# ===========================================================================
# Task 5: Resolution wiring — ledger.resolve from the real gameplay path
#
# Test 1: driving a trope to terminal status through the REAL trope path
#   (tick_tropes → _fire_one_staggered_beat) flips its ledger entry from
#   "open" to "resolved" and emits ledger.resolve (Plan 5's span, captured
#   via the real OTEL in-memory exporter).
#
# Test 2: an unresolved thread stays "open" across subsequent expansions
#   (accumulation spine — it does not silently age out; spec §7.1 "no
#   arbitrary clock").
#
# The mandatory wiring test (CLAUDE.md) lives in test_setpiece_attach_wiring.py.
# ===========================================================================


def _make_terminal_trope_def(trope_id: str) -> Any:
    """TropeDefinition-shaped object whose ladder reaches terminal in ONE
    tick once its TropeState progress is at the cap.

    This returns the PACK DEFINITION (duck-typed for
    tick_tropes' ``pack_tropes_by_id``), NOT a TropeState. The escalation
    ladder has a single beat at threshold 0.0 so it is immediately eligible
    on the first staggered-beat pass. The CALLER must set the live
    ``TropeState.progress = 1.0`` (start_trope_components appends it at
    progress 0.0); the test does that explicitly before tick_tropes. With
    progress at the cap, ``_advance_progress`` is a no-op and
    ``_fire_one_staggered_beat`` fires the single beat: then
    beats_fired==1==len(escalation) AND progress>=1.0 → terminal resolution
    (winner.status="resolved").

    This drives the REAL ``_fire_one_staggered_beat`` terminal path — not a
    test shortcut.
    """
    progression = SimpleNamespace(
        rate_per_turn=0.0,
        rate_per_day=0.0,
        accelerators=[],
        decelerators=[],
        accelerator_bonus=0.0,
        decelerator_penalty=0.0,
    )
    beat = SimpleNamespace(at=0.0, event="The trap springs!", stakes="", npcs_involved=[], roles=[])
    return SimpleNamespace(
        id=trope_id,
        passive_progression=progression,
        escalation=[beat],  # exactly ONE beat at threshold 0.0
    )


# ---------------------------------------------------------------------------
# Task 5 Test 1: terminal status via real tick_tropes → ledger entry resolved
# + ledger.resolve span emitted.
# ---------------------------------------------------------------------------


def test_resolved_trope_flips_ledger_entry_and_emits_span() -> None:
    """Task 5 Test 1.

    Drives a trope to terminal status through the REAL tick_tropes engine
    (not a test-only shortcut), then calls
    resolve_complications_for_resolved_tropes with the resolved trope id.
    Asserts:
      - the ledger thread flipped from "open" to "resolved"
      - ledger.resolve (Plan 5's span) was emitted carrying THE thread_id
      - origin↔resolution proven via the SOURCE OF TRUTH (the persisted
        ledger row): store.get_thread(thread_id).origin_region_id ==
        the attach origin AND .status == "resolved"

    Seam-1 supersession continuation: merged Plan 5's ``ledger_resolve_span``
    emits ONLY ``thread_id`` (it does NOT carry origin region — that is on
    Plan 5's ``ledger.add`` span and is durably persisted on the thread
    row). Plan 5 owns that span; Plan 6 must NOT modify it. The spec's
    "ledger.resolve carries origin region" is therefore satisfied through
    the persisted row (the source of truth), verified here via get_thread —
    NOT via a span attribute that Plan 5 does not emit.

    OTEL captured via the in-memory exporter (established pattern from
    test_commit_and_ledger_emit_spans in test_persistence.py).
    """
    import sqlite3  # noqa: PLC0415

    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415
    from sidequest.dungeon.persistence import DungeonStore  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        attach_set_piece,
        resolve_complications_for_resolved_tropes,
    )
    from sidequest.game.trope_tick import tick_tropes  # noqa: PLC0415
    from sidequest.telemetry.spans.dungeon_persist import SPAN_LEDGER_RESOLVE  # noqa: PLC0415

    # OTEL in-memory capture for ledger.resolve
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()

    trope_id = "cave_in"
    origin_region = "exp001.r5"
    trope_def = _make_terminal_trope_def(trope_id)
    pack = _make_pack(trope_def)
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])

    # Step 1: Attach the set-piece — writes an open ledger thread for cave_in.
    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        attach_set_piece(
            campaign_seed=42,
            expansion_id=1,
            region_id=origin_region,
            setpiece_id="the_trap",
            set_piece=set_piece,
            trope_components=_make_components(trope_id),
            quest_components=[],
            pack_tropes=pack,
            snapshot=snapshot,
            manifest=_FakeManifest(),
            store=store,
            threads_lit_per_expansion=10,
            threads_already_lit=0,
            started_at_depth_score=15.0,
        )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]
    conn.commit()

    # Verify the thread is open before tick. Capture its thread_id — the
    # source-of-truth row we will re-read via get_thread after resolution
    # to prove origin↔resolution end-to-end.
    open_before = store.open_threads()
    assert len(open_before) == 1
    assert open_before[0].status == "open"
    assert open_before[0].payload["ref_id"] == trope_id
    assert open_before[0].origin_region_id == origin_region
    resolved_thread_id = open_before[0].thread_id

    # Step 2: Set trope progress=1.0 so the first tick fires the beat and
    # hits the terminal condition (beats_fired==1==len(escalation) AND
    # progress>=1.0 → status="resolved"). Using the REAL tick_tropes engine.
    snapshot.active_tropes[0].progress = 1.0

    # Capture baseline for the handshake diff (mirrors the 45-20 site).
    trope_status_baseline: dict[str, str] = {t.id: t.status for t in snapshot.active_tropes}

    # Drive the trope to terminal through the REAL tick_tropes engine.
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        tick_tropes(snapshot, pack, now_turn=1)
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # Confirm the real engine flipped the status to "resolved".
    assert snapshot.active_tropes[0].status == "resolved", (
        "tick_tropes did not resolve the trope — terminal condition not met"
    )

    # Step 3: Compute the resolved-trope diff (the 45-20 handshake diff).
    # This mirrors the exact diff the handler produces.
    resolved_trope_ids = [
        t.id
        for t in snapshot.active_tropes
        if t.status == "resolved" and trope_status_baseline.get(t.id) != "resolved"
    ]
    assert resolved_trope_ids == [trope_id]

    # Step 4: Call the resolution subscription through the REAL function.
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        resolve_complications_for_resolved_tropes(
            resolved_trope_ids=resolved_trope_ids,
            store=store,
        )
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]
    conn.commit()

    # Assert 1: no open threads remain (the one trope thread was resolved).
    remaining_open = store.open_threads()
    assert remaining_open == [], (
        f"ledger thread still open after resolve_complications_for_resolved_tropes; "
        f"open threads: {remaining_open}"
    )

    # Assert 2: origin↔resolution proven through the SOURCE OF TRUTH — the
    # persisted ledger row. get_thread returns the row regardless of status;
    # assert it is now "resolved" AND still carries the attach origin region.
    # This is what the spec's "ledger.resolve carries origin region"
    # actually requires (origin is durable on the row; merged Plan 5's
    # ledger.resolve span carries only thread_id — Plan 6 must not modify
    # Plan 5's owned span; Seam-1 supersession continuation).
    resolved_row = store.get_thread(resolved_thread_id)
    assert resolved_row.status == "resolved", (
        f"persisted ledger row status is {resolved_row.status!r}, expected 'resolved'"
    )
    assert resolved_row.origin_region_id == origin_region, (
        f"persisted ledger row origin_region_id is "
        f"{resolved_row.origin_region_id!r}, expected {origin_region!r} — "
        "origin↔resolution linkage broken"
    )

    # Assert 3: the REAL ledger.resolve span (Plan 5's, via the in-memory
    # exporter) was emitted carrying THE resolved thread_id — that span
    # plus the persisted row above proves origin↔resolution end-to-end.
    finished = exporter.get_finished_spans()
    resolve_spans = [s for s in finished if s.name == SPAN_LEDGER_RESOLVE]
    assert resolve_spans, (
        "ledger.resolve span NOT emitted — Plan 5's resolve_thread span is missing"
    )
    resolve_thread_ids = {(s.attributes or {}).get("thread_id") for s in resolve_spans}
    assert resolved_thread_id in resolve_thread_ids, (
        f"ledger.resolve span did not carry the resolved thread_id "
        f"{resolved_thread_id!r}; span thread_ids: {resolve_thread_ids}"
    )


# ---------------------------------------------------------------------------
# Task 5 Test 2: unresolved thread stays "open" across subsequent expansions
# (accumulation spine — no arbitrary clock).
# ---------------------------------------------------------------------------


def test_unresolved_thread_stays_open_across_subsequent_expansions() -> None:
    """Task 5 Test 2 — spec §7.1 accumulation spine.

    An open ledger thread (from a set-piece attach) stays "open" after a
    subsequent attach_set_piece call. No arbitrary clock ages it out. This
    covers both:
      - a still-progressing trope thread (Plan 6 has a resolver for it
        but the trope has not yet hit terminal status)
      - a quest thread (Plan 6 has no resolver — Plan 7's; it also stays
        open, proving Decision O is correct: quest-thread resolution is
        Plan 7's)

    The test drives one tick that does NOT reach terminal status (progress
    starts below 1.0 and rate=0.0 so it stays below 1.0), confirms both
    threads are still open, then calls a second attach and confirms the
    original threads remain.
    """
    import sqlite3  # noqa: PLC0415

    from sidequest.dungeon.persistence import DungeonStore  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        attach_set_piece,
        resolve_complications_for_resolved_tropes,
    )
    from sidequest.game.trope_tick import tick_tropes  # noqa: PLC0415

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()

    # Trope def with NO escalation — progress never hits terminal.
    # rate_per_turn=0.0 so progress stays at 0.0 through any number of ticks.
    still_progressing_def = _make_trope_def("slow_rising_water", rate_per_turn=0.0)
    pack = _make_pack(still_progressing_def)
    snapshot = _fresh_snapshot()
    set_piece = _make_set_piece([{"name": "layout", "options": [{"value": "pit", "weight": 1.0}]}])

    # Attach 1: one trope + one quest. Two threads, both open.
    attach_set_piece(
        campaign_seed=7,
        expansion_id=1,
        region_id="exp001.r2",
        setpiece_id="flood_chamber",
        set_piece=set_piece,
        trope_components=_make_components("slow_rising_water"),
        quest_components=_make_quest_components("find_the_drain"),
        pack_tropes=pack,
        snapshot=snapshot,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=0,
        started_at_depth_score=5.0,
    )
    conn.commit()

    open_after_attach1 = store.open_threads()
    assert len(open_after_attach1) == 2, (
        f"expected 2 open threads after attach 1, got {len(open_after_attach1)}"
    )

    # Tick the trope — it does NOT resolve (no escalation ladder, rate=0.0).
    trope_status_baseline = {t.id: t.status for t in snapshot.active_tropes}
    tick_tropes(snapshot, pack, now_turn=1)

    # The trope is still progressing (not resolved).
    assert snapshot.active_tropes[0].status == "progressing", (
        "trope resolved unexpectedly — test fixture is wrong"
    )

    # Compute resolved diff — should be empty (nothing resolved).
    resolved_ids = [
        t.id
        for t in snapshot.active_tropes
        if t.status == "resolved" and trope_status_baseline.get(t.id) != "resolved"
    ]
    assert resolved_ids == [], "test fixture produced unexpected resolution"

    # Call the resolution subscription with an empty resolved set — no-op.
    resolve_complications_for_resolved_tropes(
        resolved_trope_ids=resolved_ids,
        store=store,
    )

    # Both threads still open — no arbitrary aging.
    still_open = store.open_threads()
    assert len(still_open) == 2, (
        f"threads aged out without resolution — spec §7.1 'no arbitrary clock' violated; "
        f"open={len(still_open)}"
    )
    assert all(t.status == "open" for t in still_open)

    # Attach 2: a second set-piece in the same expansion.
    # The two original threads must STILL be open after the second attach
    # (accumulation spine: ledger grows, old threads do not disappear).
    snapshot2 = _fresh_snapshot()
    another_trope_def = _make_trope_def("collapsing_bridge", rate_per_turn=0.0)
    pack2 = _make_pack(still_progressing_def, another_trope_def)
    attach_set_piece(
        campaign_seed=7,
        expansion_id=1,
        region_id="exp001.r3",
        setpiece_id="broken_bridge",
        set_piece=set_piece,
        trope_components=_make_components("collapsing_bridge"),
        quest_components=[],
        pack_tropes=pack2,
        snapshot=snapshot2,
        manifest=_FakeManifest(),
        store=store,
        threads_lit_per_expansion=10,
        threads_already_lit=2,  # the two from attach 1 already lit
        started_at_depth_score=8.0,
    )
    conn.commit()

    final_open = store.open_threads()
    assert len(final_open) == 3, (
        f"expected 3 open threads after attach 2 (2 original + 1 new), got {len(final_open)}"
    )
    # All three threads are "open" — accumulation spine intact.
    assert all(t.status == "open" for t in final_open)


# ---------------------------------------------------------------------------
# Task 5 Blocker-3 correctness: resolution semantics for the real cases.
#   (a) resolved trope with NO matching open dungeon thread → clean no-op.
#   (b) duplicate set-piece components → BOTH count-matched threads flip.
#   (c) more resolved instances than open threads / repeated calls →
#       resolve all open, surplus is a clean no-op (no double-resolve,
#       no raise).
# ---------------------------------------------------------------------------


def _store_mem() -> tuple[Any, Any]:
    import sqlite3  # noqa: PLC0415

    from sidequest.dungeon.persistence import DungeonStore  # noqa: PLC0415

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = DungeonStore(conn)
    store.ensure_schema()
    return conn, store


def test_resolution_case_a_resolved_trope_no_dungeon_thread_is_clean_noop() -> None:
    """Case (a) — the DOMINANT path. A normal resolved trope with NO
    matching open dungeon thread is a clean no-op: no NotFoundError, no
    raise, no ledger mutation. Most resolved tropes are non-dungeon tropes
    (the trope engine is global) and land here every turn."""
    from sidequest.dungeon.persistence import ComplicationThread  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        resolve_complications_for_resolved_tropes,
    )

    conn, store = _store_mem()
    # One UNRELATED open dungeon thread (ref_id "altar_collapse") — proves
    # the no-op leaves real ledger rows untouched.
    store.open_thread(
        ComplicationThread(
            thread_id="t_unrelated",
            origin_region_id="exp001.r0",
            kind="trope",
            status="open",
            started_at_depth_score=10.0,
            payload={
                "ref_id": "altar_collapse",
                "setpiece_id": "sp",
                "component_index": 0,
                "params": {},
            },
        )
    )
    conn.commit()

    # A bunch of normal resolved tropes, NONE of which has a dungeon thread.
    resolve_complications_for_resolved_tropes(
        resolved_trope_ids=["forest_ambush", "tavern_brawl", "rivals_reunite"],
        store=store,
    )

    # Clean no-op: the unrelated thread is untouched, still open.
    open_threads = store.open_threads()
    assert len(open_threads) == 1
    assert open_threads[0].thread_id == "t_unrelated"
    assert open_threads[0].status == "open"


def test_resolution_case_b_duplicate_components_both_threads_flip() -> None:
    """Case (b) — two resolved instances of the same trope_id with TWO open
    kind="trope" threads (same payload.ref_id) → BOTH flip resolved
    (count-matched). resolved_trope_ids is NOT deduped — the handshake diff
    legitimately carries the trope_id once per resolved TropeState."""
    from sidequest.dungeon.persistence import ComplicationThread  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        resolve_complications_for_resolved_tropes,
    )

    conn, store = _store_mem()
    for i in range(2):
        store.open_thread(
            ComplicationThread(
                thread_id=f"twin_{i}",
                origin_region_id="exp002.r3",
                kind="trope",
                status="open",
                started_at_depth_score=5.0,
                payload={
                    "ref_id": "twin_trap",
                    "setpiece_id": "double_trouble",
                    "component_index": i,
                    "params": {},
                },
            )
        )
    conn.commit()
    assert len(store.open_threads()) == 2

    # Two resolved instances of the same trope_id (the diff carries it
    # ONCE PER resolved TropeState — duplicate components → duplicate ids).
    resolve_complications_for_resolved_tropes(
        resolved_trope_ids=["twin_trap", "twin_trap"],
        store=store,
    )

    # BOTH threads resolved (count-matched, no naive dedup).
    assert store.open_threads() == []
    for i in range(2):
        row = store.get_thread(f"twin_{i}")
        assert row.status == "resolved", (
            f"twin_{i} not resolved — naive dedup under-resolved genuine duplicates"
        )


def test_resolution_case_c_surplus_resolved_instances_clean_noop_no_raise() -> None:
    """Case (c) — more resolved instances than open threads, AND a repeated
    call. Resolve every currently-open matching thread; surplus instances
    and a second call find no open thread and are a clean no-op — NO
    double-resolve (no spurious second ledger.resolve span), NO raise."""
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: PLC0415
        InMemorySpanExporter,
    )

    import sidequest.telemetry.spans as _spans_module  # noqa: PLC0415
    from sidequest.dungeon.persistence import ComplicationThread  # noqa: PLC0415
    from sidequest.dungeon.setpiece_attach import (  # noqa: PLC0415
        resolve_complications_for_resolved_tropes,
    )
    from sidequest.telemetry.spans.dungeon_persist import (  # noqa: PLC0415
        SPAN_LEDGER_RESOLVE,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_tracer = provider.get_tracer("test")

    conn, store = _store_mem()
    # ONE open thread, but THREE resolved instances of its ref_id.
    store.open_thread(
        ComplicationThread(
            thread_id="solo_thread",
            origin_region_id="exp003.r1",
            kind="trope",
            status="open",
            started_at_depth_score=12.0,
            payload={
                "ref_id": "ceiling_crack",
                "setpiece_id": "collapse",
                "component_index": 0,
                "params": {},
            },
        )
    )
    conn.commit()

    original_tracer_fn = _spans_module.tracer
    _spans_module.tracer = lambda: real_tracer  # type: ignore[method-assign]
    try:
        # Surplus: 3 resolved instances, only 1 open thread.
        resolve_complications_for_resolved_tropes(
            resolved_trope_ids=["ceiling_crack", "ceiling_crack", "ceiling_crack"],
            store=store,
        )
        conn.commit()
        # Repeated call (idempotent re-detect — the handshake fires every
        # turn; an already-resolved thread must NOT re-resolve).
        resolve_complications_for_resolved_tropes(
            resolved_trope_ids=["ceiling_crack"],
            store=store,
        )
        conn.commit()
    finally:
        _spans_module.tracer = original_tracer_fn  # type: ignore[method-assign]

    # The single thread resolved exactly once; no raise on the surplus.
    assert store.open_threads() == []
    row = store.get_thread("solo_thread")
    assert row.status == "resolved"

    # Exactly ONE ledger.resolve span — the surplus instances and the
    # repeated call did NOT re-resolve (no double-emit, no GM-panel lie).
    finished = exporter.get_finished_spans()
    resolve_spans = [
        s
        for s in finished
        if s.name == SPAN_LEDGER_RESOLVE and (s.attributes or {}).get("thread_id") == "solo_thread"
    ]
    assert len(resolve_spans) == 1, (
        f"expected exactly ONE ledger.resolve span for solo_thread (no "
        f"double-resolve on surplus/repeat), got {len(resolve_spans)}"
    )
