"""Tests for TurnRecord dataclass shape and immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sidequest.telemetry.turn_record import PatchSummary, TurnRecord


def _stub_snapshot():
    return object()


def _stub_delta():
    return object()


def test_turn_record_is_frozen() -> None:
    record = TurnRecord(
        turn_id=1,
        timestamp=datetime.now(UTC),
        player_id="alice",
        player_input="I look.",
        classified_intent="look",
        agent_name="narrator",
        narration="The room is dark.",
        patches_applied=[],
        snapshot_before_hash="abc",
        snapshot_after=_stub_snapshot(),
        delta=_stub_delta(),
        beats_fired=[],
        extraction_tier=1,
        token_count_in=10,
        token_count_out=20,
        agent_duration_ms=300,
        is_degraded=False,
    )
    with pytest.raises(FrozenInstanceError):
        record.turn_id = 2  # type: ignore[misc]


def test_patch_summary_is_frozen() -> None:
    p = PatchSummary(patch_type="world", fields_changed=["location"])
    with pytest.raises(FrozenInstanceError):
        p.patch_type = "combat"  # type: ignore[misc]


def test_turn_record_carries_all_fields() -> None:
    record = TurnRecord(
        turn_id=42,
        timestamp=datetime.now(UTC),
        player_id="alice",
        player_input="I attack the troll.",
        classified_intent="combat.attack",
        agent_name="combat",
        narration="You swing.",
        patches_applied=[
            PatchSummary(patch_type="combat", fields_changed=["hp"]),
        ],
        snapshot_before_hash="hash1",
        snapshot_after=_stub_snapshot(),
        delta=_stub_delta(),
        beats_fired=[("desperation", 0.7)],
        extraction_tier=2,
        token_count_in=120,
        token_count_out=240,
        agent_duration_ms=812,
        is_degraded=False,
    )
    assert record.turn_id == 42
    assert record.beats_fired == [("desperation", 0.7)]
    assert record.patches_applied[0].patch_type == "combat"
