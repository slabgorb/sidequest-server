"""Tests for the Layer-3 narrative validator pipeline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from sidequest.telemetry import watcher_hub as wh_mod  # noqa: F401
from sidequest.telemetry.turn_record import PatchSummary, TurnRecord
from sidequest.telemetry.validator import (
    TROPE_KEYWORDS_SOURCE,
    Validator,
    entity_check,
    inventory_check,
    patch_legality_check,
    subsystem_exercise_check,
    trope_alignment_check,
)


def _make_record(turn_id: int = 1) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        timestamp=datetime.now(UTC),
        player_id="alice",
        player_input="I look.",
        classified_intent="look",
        agent_name="narrator",
        narration="The room is dark.",
        patches_applied=[],
        snapshot_before_hash="h0",
        snapshot_after=object(),
        delta=object(),
        beats_fired=[],
        extraction_tier=1,
        token_count_in=10,
        token_count_out=20,
        agent_duration_ms=100,
        is_degraded=False,
    )


@pytest.mark.asyncio
async def test_validator_starts_and_drains_on_shutdown() -> None:
    v = Validator()
    await v.start()
    assert v.is_running()

    await v.submit(_make_record(turn_id=1))
    await v.shutdown(grace_seconds=2.0)

    assert not v.is_running()


@pytest.mark.asyncio
async def test_submit_drops_oldest_under_backpressure() -> None:
    v = Validator(queue_maxsize=2)
    # Don't start the consumer — let the queue fill.
    for i in range(5):
        await v.submit(_make_record(turn_id=i))

    assert v.dropped_records >= 3


class _CapturedEvents(list):
    pass


@pytest.fixture
def captured_events(monkeypatch):
    captured = _CapturedEvents()

    def fake_publish(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append({
            "event_type": event_type,
            "fields": fields,
            "component": component,
            "severity": severity,
        })

    monkeypatch.setattr(
        "sidequest.telemetry.validator.publish_event",
        fake_publish,
    )
    return captured


@pytest.mark.asyncio
async def test_entity_check_warns_on_unknown_npc(captured_events) -> None:
    """Narration mentioning an NPC not in the registry produces a
    validation_warning."""
    snapshot_after = type(
        "Snap",
        (),
        {
            "npc_registry": {},  # empty
            "discovered_regions": [],
            "inventory": type("Inv", (), {"items": []})(),
        },
    )()

    record = _make_record()
    record_dict = record.__dict__.copy()
    record_dict["narration"] = "Sir Reginald nods grimly."
    record_dict["snapshot_after"] = snapshot_after
    new_record = TurnRecord(**record_dict)

    await entity_check(new_record)

    warnings = [e for e in captured_events if e["event_type"] == "validation_warning"]
    assert warnings, "entity_check should warn on unknown NPC"
    assert "Sir Reginald" in str(warnings[0]["fields"])


@pytest.mark.asyncio
async def test_inventory_check_warns_on_narration_grab_with_no_patch(
    captured_events,
) -> None:
    record_dict = _make_record().__dict__.copy()
    record_dict["narration"] = "You grab the lantern from the shelf."
    record_dict["patches_applied"] = []
    record_dict["delta"] = type("Delta", (), {"inventory_changes": []})()
    record = TurnRecord(**record_dict)

    await inventory_check(record)
    warnings = [e for e in captured_events if e["event_type"] == "validation_warning"]
    assert any("inventory" in str(w["fields"]) for w in warnings)


@pytest.mark.asyncio
async def test_inventory_check_warns_on_silent_patch(captured_events) -> None:
    record_dict = _make_record().__dict__.copy()
    record_dict["narration"] = "You walk forward."
    record_dict["patches_applied"] = [
        PatchSummary(patch_type="world", fields_changed=["inventory.rope"]),
    ]
    record_dict["delta"] = type(
        "Delta",
        (),
        {"inventory_changes": [{"item": "rope", "delta": 1}]},
    )()
    record = TurnRecord(**record_dict)

    await inventory_check(record)
    warnings = [e for e in captured_events if e["event_type"] == "validation_warning"]
    assert any("rope" in str(w["fields"]) for w in warnings)


@pytest.mark.asyncio
async def test_patch_legality_warns_on_hp_over_max(captured_events) -> None:
    """HP > max in snapshot_after is an illegal patch outcome."""

    class _Char:
        def __init__(self, hp: int, hp_max: int) -> None:
            self.hp = hp
            self.hp_max = hp_max

    snapshot_after = type(
        "Snap",
        (),
        {
            "characters": {"alice": _Char(hp=120, hp_max=100)},
            "npc_registry": {},
        },
    )()
    record_dict = _make_record().__dict__.copy()
    record_dict["snapshot_after"] = snapshot_after
    record_dict["patches_applied"] = [
        PatchSummary(patch_type="combat", fields_changed=["characters.alice.hp"]),
    ]
    record = TurnRecord(**record_dict)

    await patch_legality_check(record)
    errors = [
        e for e in captured_events
        if e["event_type"] == "validation_warning" and e["severity"] == "error"
    ]
    assert errors, "HP-over-max should produce an error-severity warning"


@pytest.mark.asyncio
async def test_trope_alignment_warns_when_keywords_absent(
    captured_events, monkeypatch,
) -> None:
    monkeypatch.setitem(
        TROPE_KEYWORDS_SOURCE,
        "desperation",
        ["frantic", "shaking", "ragged", "trembling"],
    )

    record_dict = _make_record().__dict__.copy()
    record_dict["beats_fired"] = [("desperation", 0.7)]
    record_dict["narration"] = "You walk down the hallway calmly."
    record = TurnRecord(**record_dict)

    await trope_alignment_check(record)
    warnings = [e for e in captured_events if e["event_type"] == "validation_warning"]
    assert any(
        "trope_alignment" in str(w["fields"]) for w in warnings
    )


@pytest.mark.asyncio
async def test_subsystem_exercise_emits_per_turn_summary(captured_events) -> None:
    record = _make_record()
    await subsystem_exercise_check(record)
    summaries = [
        e for e in captured_events
        if e["event_type"] == "subsystem_exercise_summary"
    ]
    assert summaries, "subsystem_exercise_check should emit a per-turn summary"


@pytest.mark.asyncio
async def test_subsystem_exercise_emits_coverage_gap_after_silence(
    captured_events,
) -> None:
    from sidequest.telemetry.validator import _reset_subsystem_window

    _reset_subsystem_window()
    for i in range(11):
        record_dict = _make_record(turn_id=i).__dict__.copy()
        record_dict["agent_name"] = "narrator"
        await subsystem_exercise_check(TurnRecord(**record_dict))

    gaps = [e for e in captured_events if e["event_type"] == "coverage_gap"]
    assert gaps, "Expected a coverage_gap after a long subsystem silence"


@pytest.mark.asyncio
async def test_validator_emits_turn_complete_first(captured_events) -> None:
    """turn_complete is emitted before the five checks run, and carries
    fields populated from the TurnRecord."""
    v = Validator()
    await v.start()
    try:
        record = _make_record(turn_id=99)
        await v.submit(record)
        await asyncio.sleep(0.1)
    finally:
        await v.shutdown()

    completes = [e for e in captured_events if e["event_type"] == "turn_complete"]
    assert completes, "validator must emit turn_complete per TurnRecord"
    assert completes[0]["fields"]["turn_id"] == 99
    assert completes[0]["fields"]["agent_name"] == "narrator"


@pytest.mark.asyncio
async def test_trope_alignment_silent_when_keywords_present(
    captured_events, monkeypatch,
) -> None:
    monkeypatch.setitem(
        TROPE_KEYWORDS_SOURCE,
        "desperation",
        ["frantic", "shaking", "ragged"],
    )

    record_dict = _make_record().__dict__.copy()
    record_dict["beats_fired"] = [("desperation", 0.7)]
    record_dict["narration"] = "Your hands are shaking as you reach for the door."
    record = TurnRecord(**record_dict)

    await trope_alignment_check(record)
    warnings = [e for e in captured_events if e["event_type"] == "validation_warning"]
    assert not any("trope_alignment" in str(w["fields"]) for w in warnings)


@pytest.mark.asyncio
async def test_validator_emits_periodic_queue_depth(captured_events) -> None:
    """Validator surfaces queue_depth as state_transition events."""
    v = Validator()
    v._heartbeat_interval = 0.1  # speed up for the test
    await v.start()
    try:
        await v.submit(_make_record())
        await asyncio.sleep(0.3)  # let heartbeat fire
    finally:
        await v.shutdown()

    health = [
        e for e in captured_events
        if e["event_type"] == "state_transition"
        and e["component"] == "validator"
        and "queue_depth" in str(e["fields"])
    ]
    assert health, "expected validator queue_depth heartbeat"
