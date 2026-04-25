"""Tests for the Layer-3 narrative validator pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sidequest.telemetry.turn_record import TurnRecord
from sidequest.telemetry.validator import Validator


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
