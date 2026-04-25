"""Tests for the Layer-3 narrative validator pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sidequest.telemetry import watcher_hub as wh_mod  # noqa: F401
from sidequest.telemetry.turn_record import PatchSummary, TurnRecord
from sidequest.telemetry.validator import Validator, entity_check, inventory_check


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
