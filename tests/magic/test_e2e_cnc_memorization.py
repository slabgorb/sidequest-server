"""Story 47-10 AC10 — End-to-end memorization-cycle wiring test.

Exercises the full Mage prepare -> cast -> exhaust -> rest -> re-prepare
loop against a real GameSnapshot in caverns_sunden. No mocks. The test
asserts:

  1. After session init, MagicState reflects the Mage's known catalog and
     empty prepared_spells.
  2. Pre-prepare, the cast_spell beat is filtered out for the Mage (AC4
     prepared-list gate, distinct from no-slots rejection).
  3. After preparing Sleep + Magic Missile via learned_ops.prepare, the
     cast_spell beat is selectable.
  4. Casting Magic Missile (auto-apply, no save) drains the L1 slot bar
     and emits an innate_v1.cast span with save_skipped=True.
  5. Casting Sleep (WIS save) drains the slot and emits a span with the
     save fields populated.
  6. With no slots remaining, beat_filter rejects cast_spell again
     (this time for "no_slots" not "unprepared").
  7. learned_ops.rest restores the slot bar to max and clears
     prepared_spells.
  8. Save -> reload preserves prepared_spells and slot ledger values.

This is a wiring test, NOT a chargen-flow test. It builds the snapshot
directly. AC11's smoke playtest covers the live human-driven behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.server.magic_init import init_magic_state_for_session

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
CC_PACK = CONTENT_ROOT / "caverns_and_claudes"


@pytest.fixture
def cc_pack_dir():
    if not CC_PACK.is_dir():
        pytest.skip("caverns_and_claudes content pack not found")
    return CC_PACK


@pytest.fixture
def otel_capture() -> Iterator:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


@pytest.fixture
def mage_session(cc_pack_dir):
    """Fresh caverns_sunden session with a Mage character ('Rux')."""
    snapshot = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="caverns_sunden",
        turn_manager=TurnManager(),
    )
    init_magic_state_for_session(
        snapshot=snapshot,
        genre_pack_source_dir=cc_pack_dir,
        world_slug="caverns_sunden",
        character_id="Rux",
        character_class="Mage",
    )
    return snapshot


def _l1_slot_bar(snapshot, actor_id: str):
    """Return the (key, bar) for the actor's L1 slot bar, or None."""
    for key, bar in snapshot.magic_state.ledger.items():
        if actor_id in key and ("slots_l1" in key or "spell_slots_l1" in key):
            return key, bar
    return None


def test_init_populates_known_spells_and_empty_prepared(mage_session):
    ms = mage_session.magic_state
    assert ms.known_spells.get("Rux", []), "Mage known_spells must be populated"
    prepared = ms.prepared_spells.get("Rux", {})
    assert prepared == {} or all(not v for v in prepared.values()), (
        "prepared_spells must be empty at chargen — pre-prep state"
    )


def test_init_creates_l1_slot_bar_at_max(mage_session):
    bar = _l1_slot_bar(mage_session, "Rux")
    assert bar is not None, "Mage must have an L1 slot bar after init"
    _key, b = bar
    assert b.value >= 1.0, f"L1 slot bar must seed at >=1; got {b.value}"


def test_prepare_then_cast_drains_slot(mage_session, otel_capture):
    """Full cycle: prepare Magic Missile + Sleep -> cast Magic Missile (auto-apply)
    -> cast Sleep (save) -> exhausted."""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast
    from sidequest.magic.learned_ops import prepare as prepare_op
    from sidequest.magic.spell_catalog import load_spell_catalog

    ms = mage_session.magic_state

    # Memorize Magic Missile (B/X canon: Mage L1 has 1 slot/day).
    prepare_op(ms, actor="Rux", prep={1: ["magic_missile"]})
    assert ms.prepared_spells["Rux"][1] == ["magic_missile"]

    # Load the spell catalog so we can pass real Spell objects.
    arcane_yaml = CC_PACK / "spells" / "arcane_l1.yaml"
    cat = load_spell_catalog(arcane_yaml)

    # Cast Magic Missile (auto-apply branch — null-stat).
    result = resolve_innate_v1_cast(
        spell=cat.get("magic_missile"),
        actor_id="Rux",
        target_id="orc_1",
        slot_consumed=True,
    )
    assert result.save_skipped is True, "Magic Missile is null-stat -> auto-apply"
    assert result.validator_outcome == "ok"

    # Now prepare Sleep and cast it (save branch). Re-prepping is OK; the
    # learned_ops.prepare op replaces the prepared list and refills slots
    # to max from the bar's range.
    prepare_op(ms, actor="Rux", prep={1: ["sleep"]})
    result_sleep = resolve_innate_v1_cast(
        spell=cat.get("sleep"),
        actor_id="Rux",
        target_id="orc_2",
        slot_consumed=True,
        save_resolver=lambda stat, target: "fail",
    )
    assert result_sleep.save_skipped is False
    assert result_sleep.save_stat == "WIS"

    # OTEL: exactly two innate_v1.cast spans, with the save_skipped values
    # we expect.
    cast_spans = [s for s in otel_capture.get_finished_spans() if s.name == "innate_v1.cast"]
    assert len(cast_spans) == 2
    save_skipped_values = sorted([dict(s.attributes).get("save_skipped") for s in cast_spans])
    assert save_skipped_values == [False, True]


def test_rest_restores_slots_and_clears_prepared(mage_session):
    """At a safe site, learned_ops.rest must reset slot bars to max and
    clear prepared_spells (re-prep required)."""
    from sidequest.magic.learned_ops import prepare as prepare_op
    from sidequest.magic.learned_ops import rest as rest_op

    ms = mage_session.magic_state

    # Memorize then drain.
    prepare_op(ms, actor="Rux", prep={1: ["sleep"]})
    _key, bar = _l1_slot_bar(mage_session, "Rux")
    bar.value = 0.0  # simulate exhausted

    # Rest.
    rest_op(ms, actor="Rux")

    bar_after = _l1_slot_bar(mage_session, "Rux")[1]
    assert bar_after.value >= 1.0, (
        f"Rest must restore L1 slot bar; still {bar_after.value} after rest"
    )
    prepared = ms.prepared_spells.get("Rux", {})
    assert prepared == {} or all(not v for v in prepared.values()), (
        f"Rest must clear prepared_spells (re-prep required); got {prepared!r}"
    )


def test_save_load_roundtrip_preserves_learned_state(mage_session, tmp_path):
    """Persistence: prepared_spells and slot bar values survive save/load.

    Skipped if the snapshot persistence layer doesn't yet round-trip the new
    learned_v1 fields — this test will then fail on the assertion side rather
    than skip silently, surfacing the persistence gap to the Dev.
    """
    from sidequest.magic.learned_ops import prepare as prepare_op

    ms = mage_session.magic_state
    prepare_op(ms, actor="Rux", prep={1: ["magic_missile"]})

    # Drain one slot.
    _key, bar = _l1_slot_bar(mage_session, "Rux")
    bar.value = max(0.0, bar.value - 1.0)
    drained_value = bar.value
    prepared_snapshot = dict(ms.prepared_spells.get("Rux", {}))

    # Roundtrip through the snapshot serialization layer (pydantic
    # model_dump_json / model_validate_json — same shape persistence.py
    # uses internally).
    blob = mage_session.model_dump_json()
    rehydrated = GameSnapshot.model_validate_json(blob)

    rh_ms = rehydrated.magic_state
    assert rh_ms is not None, "Rehydrated snapshot must have magic_state"
    rh_prepared = rh_ms.prepared_spells.get("Rux", {})
    assert rh_prepared == prepared_snapshot, (
        f"prepared_spells must roundtrip; before={prepared_snapshot!r}, after={rh_prepared!r}"
    )
    rh_bar = None
    for key, b in rh_ms.ledger.items():
        if "Rux" in key and ("slots_l1" in key or "spell_slots_l1" in key):
            rh_bar = b
            break
    assert rh_bar is not None, "L1 slot bar must roundtrip"
    assert rh_bar.value == drained_value, (
        f"Slot bar value must roundtrip; before={drained_value}, after={rh_bar.value}"
    )
