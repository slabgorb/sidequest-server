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
