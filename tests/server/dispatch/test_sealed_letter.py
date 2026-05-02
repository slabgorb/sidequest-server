"""Sealed-letter resolution handler tests (T3 of the dogfight port).

Ports the test surface for ``sidequest-api/crates/sidequest-server/src/dispatch/sealed_letter.rs``.
The Rust source resolves simultaneous-commit encounters where two pilots
("red" / "blue") each commit a maneuver privately and the engine looks
up the cross-product cell in an ``InteractionTable`` (ADR-077, Epic 38).

The tests cover:
  - happy path (cell found, deltas merged, outcome returned)
  - error paths (missing key, illegal maneuver, no matching cell)
  - per_actor_state merge semantics (preserve existing keys, no cross-actor pollution)
  - extend-and-return rule (no_hit + opening_fast → geometric reset)
  - OTEL span emission (3 spans fire in expected order with correct attrs)
  - wiring test that loads the real space_opera dogfight content
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    StructuredEncounter,
)
from sidequest.genre.models.rules import InteractionCell, InteractionTable
from sidequest.server.dispatch.sealed_letter import (
    _MERGE_STARTING_GEOMETRY,
    SealedLetterOutcome,
    resolve_sealed_letter_lookup,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_encounter(
    *,
    red_state: dict | None = None,
    blue_state: dict | None = None,
) -> StructuredEncounter:
    """Build a minimal dogfight-style encounter with red/blue actors."""
    actors = [
        EncounterActor(
            name="Red Pilot",
            role="red",
            side="player",
            per_actor_state=dict(red_state or {}),
        ),
        EncounterActor(
            name="Blue Pilot",
            role="blue",
            side="opponent",
            per_actor_state=dict(blue_state or {}),
        ),
    ]
    return StructuredEncounter(
        encounter_type="dogfight",
        player_metric=EncounterMetric(name="hits", current=0, threshold=3),
        opponent_metric=EncounterMetric(name="hits", current=0, threshold=3),
        actors=actors,
    )


def _make_table(*cells: InteractionCell) -> InteractionTable:
    return InteractionTable(
        version="0.1.0",
        starting_state="merge",
        maneuvers_consumed=["straight", "bank", "loop", "kill_rotation"],
        cells=list(cells),
    )


def _hit_cell() -> InteractionCell:
    """A cell where blue scores a hit (gun_solution=true)."""
    return InteractionCell(
        pair=["straight", "loop"],
        name="Blue reverses onto Red's six",
        shape="passive vs offense",
        red_view={
            "target_bearing": "06",
            "closure": "opening",
            "gun_solution": False,
        },
        blue_view={
            "target_bearing": "12",
            "closure": "opening",
            "gun_solution": True,
        },
        narration_hint="Blue pulls the loop, Red is in the gunsight.",
    )


def _no_hit_cell_with_opening_fast() -> InteractionCell:
    """A cell where neither actor hits AND blue closure becomes opening_fast."""
    return InteractionCell(
        pair=["bank", "kill_rotation"],
        name="Red banks past the back-shot",
        shape="no_hit",
        red_view={
            "target_bearing": "06",
            "closure": "opening",
            "gun_solution": False,
        },
        blue_view={
            "target_bearing": "12",
            "closure": "opening_fast",
            "gun_solution": False,
        },
        narration_hint="Red breaks. Blue's flip lands on empty space.",
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_outcome_and_merges_deltas() -> None:
    enc = _make_encounter()
    table = _make_table(_hit_cell())

    outcome = resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "loop"},
        table,
    )

    assert isinstance(outcome, SealedLetterOutcome)
    assert outcome.cell_name == "Blue reverses onto Red's six"
    assert outcome.red_maneuver == "straight"
    assert outcome.blue_maneuver == "loop"
    assert outcome.narration_hint == "Blue pulls the loop, Red is in the gunsight."
    assert outcome.extend_and_return_triggered is False

    red = next(a for a in enc.actors if a.role == "red")
    blue = next(a for a in enc.actors if a.role == "blue")
    assert red.per_actor_state["target_bearing"] == "06"
    assert red.per_actor_state["gun_solution"] is False
    assert blue.per_actor_state["target_bearing"] == "12"
    assert blue.per_actor_state["gun_solution"] is True


# ---------------------------------------------------------------------------
# 2. Missing commit key
# ---------------------------------------------------------------------------


def test_missing_red_commit_raises_value_error() -> None:
    enc = _make_encounter()
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match="missing 'red' key"):
        resolve_sealed_letter_lookup(enc, {"blue": "loop"}, table)


def test_missing_blue_commit_raises_value_error() -> None:
    enc = _make_encounter()
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match="missing 'blue' key"):
        resolve_sealed_letter_lookup(enc, {"red": "straight"}, table)


# ---------------------------------------------------------------------------
# 3. Illegal maneuver
# ---------------------------------------------------------------------------


def test_illegal_red_maneuver_raises_value_error() -> None:
    enc = _make_encounter()
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match="not in maneuvers_consumed"):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "barrel_roll", "blue": "loop"},
            table,
        )


def test_illegal_blue_maneuver_raises_value_error() -> None:
    enc = _make_encounter()
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match="not in maneuvers_consumed"):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "straight", "blue": "split_s"},
            table,
        )


# ---------------------------------------------------------------------------
# 4. No matching cell
# ---------------------------------------------------------------------------


def test_no_matching_cell_raises_key_error() -> None:
    enc = _make_encounter()
    # Table only has (straight, loop) but caller commits (bank, bank).
    table = _make_table(_hit_cell())

    with pytest.raises(KeyError, match=r"no interaction cell"):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "bank", "blue": "bank"},
            table,
        )


# ---------------------------------------------------------------------------
# 5. Existing per_actor_state keys preserved (merge, not replace)
# ---------------------------------------------------------------------------


def test_existing_per_actor_state_keys_preserved() -> None:
    enc = _make_encounter(
        red_state={"hull": 100, "energy": 80},
        blue_state={"hull": 100, "energy": 60},
    )
    table = _make_table(_hit_cell())

    resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "loop"},
        table,
    )

    red = next(a for a in enc.actors if a.role == "red")
    blue = next(a for a in enc.actors if a.role == "blue")
    # Pre-existing keys preserved
    assert red.per_actor_state["hull"] == 100
    assert red.per_actor_state["energy"] == 80
    assert blue.per_actor_state["hull"] == 100
    assert blue.per_actor_state["energy"] == 60
    # New keys merged
    assert red.per_actor_state["target_bearing"] == "06"
    assert blue.per_actor_state["target_bearing"] == "12"


# ---------------------------------------------------------------------------
# 6. Cross-actor isolation
# ---------------------------------------------------------------------------


def test_cross_actor_isolation_no_pollution() -> None:
    enc = _make_encounter()
    # Cell where red_view and blue_view have DIFFERENT keys, so we can prove
    # red's view doesn't bleed into blue's state and vice versa.
    cell = InteractionCell(
        pair=["straight", "loop"],
        name="cross_isolation",
        shape="test",
        red_view={"red_only_key": "red_value"},
        blue_view={"blue_only_key": "blue_value"},
        narration_hint="isolation",
    )
    table = _make_table(cell)

    resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "loop"},
        table,
    )

    red = next(a for a in enc.actors if a.role == "red")
    blue = next(a for a in enc.actors if a.role == "blue")
    assert "red_only_key" in red.per_actor_state
    assert "blue_only_key" not in red.per_actor_state
    assert "blue_only_key" in blue.per_actor_state
    assert "red_only_key" not in blue.per_actor_state


# ---------------------------------------------------------------------------
# 7. Extend-and-return triggers (no_hit + opening_fast)
# ---------------------------------------------------------------------------


def test_extend_and_return_triggers_resets_geometry_preserves_energy() -> None:
    enc = _make_encounter(
        red_state={"viewer_energy": 45, "target_energy": 30},
        blue_state={"viewer_energy": 20, "target_energy": 50},
    )
    table = _make_table(_no_hit_cell_with_opening_fast())

    outcome = resolve_sealed_letter_lookup(
        enc,
        {"red": "bank", "blue": "kill_rotation"},
        table,
    )

    assert outcome.extend_and_return_triggered is True

    for actor in enc.actors:
        # Geometric fields reset to merge starting state
        assert actor.per_actor_state["target_bearing"] == "12"
        assert actor.per_actor_state["target_range"] == "close"
        assert actor.per_actor_state["target_aspect"] == "head_on"
        assert actor.per_actor_state["closure"] == "closing_fast"
        assert actor.per_actor_state["gun_solution"] is False

    # Energy preserved
    red = next(a for a in enc.actors if a.role == "red")
    blue = next(a for a in enc.actors if a.role == "blue")
    assert red.per_actor_state["viewer_energy"] == 45
    assert red.per_actor_state["target_energy"] == 30
    assert blue.per_actor_state["viewer_energy"] == 20
    assert blue.per_actor_state["target_energy"] == 50


# ---------------------------------------------------------------------------
# 8. Extend-and-return does NOT trigger when shape != no_hit
# ---------------------------------------------------------------------------


def test_extend_and_return_skipped_when_hit_landed() -> None:
    """Even with opening_fast set, a cell whose shape is not no_hit must not reset.

    The Rust source actually keys this on whether ANY actor scored a hit
    (gun_solution=true) — a hit suppresses the reset regardless of the
    cell's textual ``shape`` field. We assert that observable behaviour:
    a hit cell + opening_fast does NOT reset.
    """
    enc = _make_encounter()
    cell = InteractionCell(
        pair=["straight", "loop"],
        name="Blue reverses onto Red's six",
        shape="passive vs offense — offense scores",
        red_view={
            "target_bearing": "06",
            "closure": "opening_fast",  # opening_fast set but...
            "gun_solution": False,
        },
        blue_view={
            "target_bearing": "12",
            "closure": "opening",
            "gun_solution": True,  # ...someone got the hit
        },
        narration_hint="Hit landed.",
    )
    table = _make_table(cell)

    outcome = resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "loop"},
        table,
    )

    assert outcome.extend_and_return_triggered is False
    # Geometry should reflect the cell deltas, NOT the merge reset
    red = next(a for a in enc.actors if a.role == "red")
    assert red.per_actor_state["target_bearing"] == "06"
    assert red.per_actor_state["closure"] == "opening_fast"


# ---------------------------------------------------------------------------
# 9. OTEL spans emitted in expected order with expected attrs
# ---------------------------------------------------------------------------


def test_otel_spans_emitted_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from sidequest.telemetry import spans as spans_module

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")

    monkeypatch.setattr(spans_module, "tracer", lambda: test_tracer)

    enc = _make_encounter()
    table = _make_table(_hit_cell())
    resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "loop"},
        table,
    )

    finished = exporter.get_finished_spans()
    names = [s.name for s in finished]

    # All three span types must fire.
    assert spans_module.SPAN_DOGFIGHT_CONFRONTATION_STARTED in names
    # SPAN_DOGFIGHT_MANEUVER_COMMITTED fires twice (once per actor)
    maneuver_spans = [
        s for s in finished if s.name == spans_module.SPAN_DOGFIGHT_MANEUVER_COMMITTED
    ]
    assert len(maneuver_spans) == 2, (
        f"expected 2 maneuver_committed spans, got {len(maneuver_spans)}"
    )
    assert spans_module.SPAN_DOGFIGHT_CELL_RESOLVED in names

    # Order: confrontation_started → maneuver_committed (×2) → cell_resolved.
    # Spans close in LIFO when nested or in start order when sequential —
    # our handler emits sequentially so finish order matches start order.
    started_idx = names.index(spans_module.SPAN_DOGFIGHT_CONFRONTATION_STARTED)
    cell_idx = names.index(spans_module.SPAN_DOGFIGHT_CELL_RESOLVED)
    maneuver_indices = [
        i for i, n in enumerate(names) if n == spans_module.SPAN_DOGFIGHT_MANEUVER_COMMITTED
    ]
    assert started_idx < min(maneuver_indices)
    assert max(maneuver_indices) < cell_idx

    # Verify attribute payloads on each span.
    started = next(
        s for s in finished if s.name == spans_module.SPAN_DOGFIGHT_CONFRONTATION_STARTED
    )
    assert started.attributes["encounter_type"] == "dogfight"
    assert started.attributes["red_actor"] == "Red Pilot"
    assert started.attributes["blue_actor"] == "Blue Pilot"

    maneuver_attrs = {(s.attributes["actor"], s.attributes["maneuver"]) for s in maneuver_spans}
    assert maneuver_attrs == {("Red Pilot", "straight"), ("Blue Pilot", "loop")}

    cell_resolved = next(s for s in finished if s.name == spans_module.SPAN_DOGFIGHT_CELL_RESOLVED)
    assert cell_resolved.attributes["cell_name"] == "Blue reverses onto Red's six"
    assert cell_resolved.attributes["shape"] == "passive vs offense"
    assert cell_resolved.attributes["red_maneuver"] == "straight"
    assert cell_resolved.attributes["blue_maneuver"] == "loop"
    assert cell_resolved.attributes["extend_and_return_triggered"] is False


# ---------------------------------------------------------------------------
# 10. Wiring test — real space_opera dogfight content
# ---------------------------------------------------------------------------


def _content_root() -> Path:
    """Locate sidequest-content from this test file's location.

    tests/server/dispatch/test_sealed_letter.py → ../../../../sidequest-content
    """
    here = Path(__file__).resolve()
    candidate = here.parents[4] / "sidequest-content"
    if not candidate.exists():
        pytest.skip(f"sidequest-content not available at {candidate}")
    return candidate


def test_wiring_real_space_opera_dogfight_table() -> None:
    """Load the actual space_opera pack, find the dogfight confrontation,
    pull its interaction_table, and resolve a real (straight, bank) commit.

    This is the load-bearing wiring test per CLAUDE.md: it proves the new
    handler reads the real on-disk content shape, not just hand-rolled
    fixture data.
    """
    from sidequest.genre.loader import load_genre_pack

    content_root = _content_root()
    pack = load_genre_pack(content_root / "genre_packs" / "space_opera")

    # Find a confrontation that ships an interaction_table.
    conf = next(
        (
            c
            for c in (pack.rules.confrontations if pack.rules else [])
            if c.interaction_table is not None
        ),
        None,
    )
    if conf is None:
        pytest.skip("space_opera has no confrontation with an interaction_table")
    table = conf.interaction_table
    assert table is not None  # for type narrowing

    # The dogfight MVP table ships with [straight, bank, loop, kill_rotation].
    assert "straight" in table.maneuvers_consumed
    assert "bank" in table.maneuvers_consumed

    enc = _make_encounter()
    outcome = resolve_sealed_letter_lookup(
        enc,
        {"red": "straight", "blue": "bank"},
        table,
    )

    # The cell at (straight, bank) is "Red drills through, Blue breaks".
    assert outcome.cell_name  # non-empty
    assert outcome.narration_hint  # non-empty
    assert outcome.red_maneuver == "straight"
    assert outcome.blue_maneuver == "bank"

    # Both actors got their per_actor_state populated from the cell.
    red = next(a for a in enc.actors if a.role == "red")
    blue = next(a for a in enc.actors if a.role == "blue")
    assert red.per_actor_state, "red view did not apply"
    assert blue.per_actor_state, "blue view did not apply"


# ---------------------------------------------------------------------------
# 11. Missing actor for required role raises (no silent fallback)
# ---------------------------------------------------------------------------


def _encounter_missing_role(role_to_drop: str) -> StructuredEncounter:
    """Encounter that's missing one of the required dogfight roles."""
    keep = [a for a in _make_encounter().actors if a.role != role_to_drop]
    return StructuredEncounter(
        encounter_type="dogfight",
        player_metric=EncounterMetric(name="hits", current=0, threshold=3),
        opponent_metric=EncounterMetric(name="hits", current=0, threshold=3),
        actors=keep,
    )


def test_missing_red_actor_raises() -> None:
    enc = _encounter_missing_role("red")
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match=r"role\(s\) \['red'\]"):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "straight", "blue": "loop"},
            table,
        )


def test_missing_blue_actor_raises() -> None:
    enc = _encounter_missing_role("blue")
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError, match=r"role\(s\) \['blue'\]"):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "straight", "blue": "loop"},
            table,
        )


def test_no_spans_emitted_when_actor_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation must fail BEFORE any OTEL span fires.

    If validation emitted spans first the GM panel would see a
    "confrontation_started" event with empty actor names — the exact
    silent-fallback lie this fix closes.
    """
    from sidequest.telemetry import spans as spans_module

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")
    monkeypatch.setattr(spans_module, "tracer", lambda: test_tracer)

    enc = _encounter_missing_role("red")
    table = _make_table(_hit_cell())

    with pytest.raises(ValueError):
        resolve_sealed_letter_lookup(
            enc,
            {"red": "straight", "blue": "loop"},
            table,
        )

    finished = exporter.get_finished_spans()
    dogfight_spans = [s for s in finished if s.name.startswith("dogfight.")]
    assert dogfight_spans == [], (
        f"expected no dogfight.* spans on validation failure, "
        f"got: {[s.name for s in dogfight_spans]}"
    )


# ---------------------------------------------------------------------------
# 12. Content/code drift regression — _MERGE_STARTING_GEOMETRY vs. YAML
# ---------------------------------------------------------------------------


def test_merge_starting_geometry_matches_descriptor_schema() -> None:
    """Hardcoded merge geometry must stay in sync with content YAML.

    If this test fails, either:
    - Content tuned the merge starting state — update _MERGE_STARTING_GEOMETRY
    - Engine drifted from content — fix the engine

    Drift between content and engine is a CLAUDE.md violation. T5 will
    replace the hardcoded constant with a runtime read from
    descriptor_schema.starting_states; until then this test is the guard.
    """
    import yaml

    schema_path = (
        Path(__file__).resolve().parents[4]
        / "sidequest-content/genre_packs/space_opera/dogfight/descriptor_schema.yaml"
    )
    if not schema_path.exists():
        pytest.skip(f"sidequest-content not available at {schema_path}")

    schema = yaml.safe_load(schema_path.read_text())
    merge = next(s for s in schema["starting_states"] if s["id"] == "merge")
    descriptor = merge["initial_descriptor"]

    for key, expected in _MERGE_STARTING_GEOMETRY.items():
        assert descriptor[key] == expected, (
            f"Drift: _MERGE_STARTING_GEOMETRY[{key!r}] = {expected!r} "
            f"but descriptor_schema.yaml merge.initial_descriptor[{key!r}] "
            f"= {descriptor[key]!r}"
        )
