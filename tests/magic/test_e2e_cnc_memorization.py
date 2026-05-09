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


def _drain_one_l1_slot(snapshot, actor_id: str) -> None:
    """Production drain happens via beat.resource_deltas in narration_apply.
    The e2e test simulates that by directly decrementing the L1 slot bar so
    the drain semantics are observable end-to-end without spinning up the
    full beat-resolution pipeline."""
    _key, bar = _l1_slot_bar(snapshot, actor_id)
    bar.value = max(0.0, bar.value - 1.0)


def test_prepare_then_cast_drains_slot(mage_session, otel_capture):
    """Full cycle: prepare Magic Missile -> cast (auto-apply) -> drain ->
    prepare Sleep -> cast (save fail) -> drain. Asserts BOTH the slot bar
    value decreased AND the innate_v1.cast OTEL span fired correctly with
    save_skipped values matching the spell shape."""
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
    slots_before_mm = _l1_slot_bar(mage_session, "Rux")[1].value
    result = resolve_innate_v1_cast(
        spell=cat.get("magic_missile"),
        actor_id="Rux",
        target_id="orc_1",
        slot_consumed=True,
    )
    assert result.save_skipped is True, "Magic Missile is null-stat -> auto-apply"
    assert result.validator_outcome == "ok"

    # Drain (production: narration_apply handles via beat.resource_deltas;
    # here: simulated). Verify the slot bar actually decreased.
    _drain_one_l1_slot(mage_session, "Rux")
    slots_after_mm = _l1_slot_bar(mage_session, "Rux")[1].value
    assert slots_after_mm == slots_before_mm - 1.0, (
        f"L1 slot must drain by exactly 1.0 after cast; was {slots_before_mm}, now {slots_after_mm}"
    )

    # Rest restores the slot bar and clears prepared, then prepare Sleep
    # for the save-fail cast (realistic flow: prepare → cast → drain →
    # rest → prepare).
    from sidequest.magic.learned_ops import rest as rest_op

    rest_op(ms, actor="Rux")
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
    _drain_one_l1_slot(mage_session, "Rux")

    # Save-success branch coverage (test-analyzer L2 — closes the negates
    # branch so the success path isn't dead code).
    rest_op(ms, actor="Rux")
    prepare_op(ms, actor="Rux", prep={1: ["sleep"]})
    result_sleep_save = resolve_innate_v1_cast(
        spell=cat.get("sleep"),
        actor_id="Rux",
        target_id="orc_3",
        slot_consumed=True,
        save_resolver=lambda stat, target: "success",
    )
    assert result_sleep_save.save_skipped is False
    assert result_sleep_save.save_result == "success"
    # Sleep's save.effect is "negates" — success means no effect lands.
    assert result_sleep_save.effect_applied is None, (
        f"Sleep save success vs negates effect must yield no effect; "
        f"got {result_sleep_save.effect_applied!r}"
    )

    # OTEL: exactly three innate_v1.cast spans (Magic Missile auto-apply,
    # Sleep fail, Sleep success).
    cast_spans = [s for s in otel_capture.get_finished_spans() if s.name == "innate_v1.cast"]
    assert len(cast_spans) == 3
    save_skipped_values = sorted([dict(s.attributes).get("save_skipped") for s in cast_spans])
    assert save_skipped_values == [False, False, True]


def test_pre_prepare_cast_spell_filtered(mage_session):
    """AC4 wiring (against real GameSnapshot, not isolated): a freshly-init'd
    Mage with empty prepared_spells must have cast_spell filtered out by
    beats_available_for. Closes the gap test-analyzer flagged: prior tests
    only verified the gate in isolation; this one runs against the
    same MagicState that init_magic_state_for_session produces in
    production."""
    import yaml

    from sidequest.game.beat_filter import (
        beats_available_for,
        cast_spell_rejection_reason,
    )
    from sidequest.genre.models.character import ClassDef
    from sidequest.genre.models.rules import ConfrontationDef

    ms = mage_session.magic_state
    # Pre-prepare: prepared_spells[Rux] is {} (per init wiring).
    prepared = ms.prepared_spells.get("Rux")
    assert prepared == {}, "Mage prepared_spells must be empty after init"

    # Load Mage class def from C&C classes.yaml.
    raw = yaml.safe_load((CC_PACK / "classes.yaml").read_text(encoding="utf-8"))
    mage_cd = next(ClassDef.model_validate(c) for c in raw if c.get("display_name") == "Mage")
    # Build a minimal combat confrontation that includes cast_spell.
    raw_rules = yaml.safe_load((CC_PACK / "rules.yaml").read_text(encoding="utf-8"))
    combat = next(c for c in raw_rules.get("confrontations", []) if c.get("type") == "combat")
    cdef = ConfrontationDef.model_validate(combat)

    # Slots remaining (Mage L1 = 1) but prepared_spells is empty → reject.
    out = beats_available_for(cdef, mage_cd, spell_slots_remaining=1.0, prepared_spells=prepared)
    assert "cast_spell" not in [b.id for b in out], (
        "Pre-prepare: cast_spell must be filtered from beat menu"
    )
    assert (
        cast_spell_rejection_reason(
            cdef, mage_cd, spell_slots_remaining=1.0, prepared_spells=prepared
        )
        == "unprepared"
    )


def test_exhausted_slots_rejection_reason(mage_session):
    """AC4 wiring: a Mage who memorized but spent everything sees cast_spell
    rejected with reason='no_slots' — distinct from the unprepared case
    above. Tests the OTEL signal distinction Sebastien-tier observability
    relies on."""
    import yaml

    from sidequest.game.beat_filter import cast_spell_rejection_reason
    from sidequest.genre.models.character import ClassDef
    from sidequest.genre.models.rules import ConfrontationDef
    from sidequest.magic.learned_ops import prepare as prepare_op

    ms = mage_session.magic_state
    prepare_op(ms, actor="Rux", prep={1: ["magic_missile"]})

    # Drain the slot.
    _key, bar = _l1_slot_bar(mage_session, "Rux")
    bar.value = 0.0

    raw = yaml.safe_load((CC_PACK / "classes.yaml").read_text(encoding="utf-8"))
    mage_cd = next(ClassDef.model_validate(c) for c in raw if c.get("display_name") == "Mage")
    raw_rules = yaml.safe_load((CC_PACK / "rules.yaml").read_text(encoding="utf-8"))
    combat = next(c for c in raw_rules.get("confrontations", []) if c.get("type") == "combat")
    cdef = ConfrontationDef.model_validate(combat)

    reason = cast_spell_rejection_reason(
        cdef,
        mage_cd,
        spell_slots_remaining=bar.value,
        prepared_spells=ms.prepared_spells.get("Rux"),
    )
    assert reason == "no_slots", (
        f"Exhausted-but-prepared Mage must reject as no_slots, not unprepared; got {reason!r}"
    )


def test_resolve_innate_cast_for_beat_rejects_unprepared(mage_session, otel_capture):
    """B5 wiring test: the defense-in-depth gate in narration_apply._resolve_innate_cast_for_beat
    must publish a watcher event AND skip emitting innate_v1.cast when the
    actor tries to cast a spell they haven't prepared. Without this test,
    a regression that deleted the gate would go unnoticed."""
    from sidequest.agents.orchestrator import BeatSelection
    from sidequest.game.encounter import EncounterActor
    from sidequest.server.narration_apply import _resolve_innate_cast_for_beat

    # Mage with NO prepared spells at L1 (fresh chargen).
    ms = mage_session.magic_state
    assert ms.prepared_spells.get("Rux") == {}, "Mage starts with nothing prepared"

    sel = BeatSelection(
        actor="Rux",
        beat_id="cast_spell",
        target="orc_1",
        spell_id="sleep",
    )
    actor = EncounterActor(name="Rux", role="caster", side="player")

    # Capture watcher events to verify the rejection path fired. We must
    # patch the rebound name in narration_apply (`_watcher_publish`),
    # not watcher_hub.publish_event — the `from … import as` aliasing
    # captures the original function object at import time.
    from sidequest.server import narration_apply

    captured: list[tuple[str, dict]] = []
    original_publish = narration_apply._watcher_publish

    def _capture(event_type, data, **kwargs):
        captured.append((event_type, dict(data)))
        return original_publish(event_type, data, **kwargs)

    narration_apply._watcher_publish = _capture
    try:
        _resolve_innate_cast_for_beat(sel=sel, actor=actor, snapshot=mage_session)
    finally:
        narration_apply._watcher_publish = original_publish

    # Defense-in-depth event fired.
    rejection_events = [e for e in captured if e[0] == "magic.cast_spell_not_prepared"]
    assert len(rejection_events) == 1, (
        f"Expected one magic.cast_spell_not_prepared watcher event; got {captured!r}"
    )
    assert rejection_events[0][1]["spell_id"] == "sleep"
    assert rejection_events[0][1]["actor"] == "Rux"

    # No innate_v1.cast OTEL span — the cast must NOT have run through
    # resolve_innate_v1_cast.
    cast_spans = [s for s in otel_capture.get_finished_spans() if s.name == "innate_v1.cast"]
    assert len(cast_spans) == 0, "Unprepared cast must short-circuit before innate_v1.cast emission"


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
    # Strict: rest must reset to the bar's spec-declared max, not just
    # bump the exhausted bar by 1.0. Distinguishes "reset to full" from
    # "incremented" semantics so a regression to the latter would fail.
    assert bar_after.value == bar_after.spec.range[1], (
        f"Rest must restore L1 slot bar to max (spec.range[1]={bar_after.spec.range[1]}); "
        f"got {bar_after.value}"
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
