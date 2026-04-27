"""Wiring tests: phase-timing fields flow from TurnRecord through Validator
and into the turn_complete event payload.

T1 unit-tested PhaseTimings in isolation. T7 (these tests) prove the data
actually crosses the validator boundary into the event the GM panel
consumes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sidequest.telemetry.turn_record import TurnRecord
from sidequest.telemetry.validator import Validator


def _make_record(**overrides: Any) -> TurnRecord:
    base: dict[str, Any] = dict(
        turn_id=1,
        timestamp=datetime.now(UTC),
        player_id="p1",
        player_input="hello",
        classified_intent="speak",
        agent_name="narrator",
        narration="The wind picks up.",
        patches_applied=[],
        snapshot_before_hash="x",
        snapshot_after=object(),
        delta=None,
        beats_fired=[],
        extraction_tier=1,
        token_count_in=10,
        token_count_out=20,
        agent_duration_ms=14336,
        is_degraded=False,
        phase_durations_ms={"preprocess_llm": 87000, "narrator_subprocess": 14336},
        phase_call_counts={"preprocess_llm": 1, "narrator_subprocess": 1},
        total_duration_ms=101000,
    )
    base.update(overrides)
    return TurnRecord(**base)


@pytest.mark.asyncio
async def test_validator_emits_phase_durations_in_turn_complete(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_publish(
        event_type: str,
        payload: dict[str, Any],
        *,
        component: str = "sidequest-server",  # noqa: ARG001
        severity: str = "info",  # noqa: ARG001
    ) -> None:
        if event_type == "turn_complete":
            captured.append(payload)

    monkeypatch.setattr(
        "sidequest.telemetry.validator.publish_event",
        fake_publish,
    )

    v = Validator()
    # Pin to the emission codepath only; real checks aren't needed for this
    # wiring guarantee and would re-publish noise into ``captured``.
    v._checks = []  # noqa: SLF001 — intentional test seam
    await v._validate(_make_record())  # noqa: SLF001 — testing internal emission

    assert len(captured) == 1
    payload = captured[0]
    assert payload["phase_durations_ms"] == {
        "preprocess_llm": 87000,
        "narrator_subprocess": 14336,
    }
    assert payload["phase_call_counts"] == {
        "preprocess_llm": 1,
        "narrator_subprocess": 1,
    }
    assert payload["total_duration_ms"] == 101000
    assert payload["agent_duration_ms"] == 14336
    # _unaccounted_ms = max(0, 101000 - (87000 + 14336)) = max(0, -336) = 0
    assert payload["_unaccounted_ms"] == 0


@pytest.mark.asyncio
async def test_total_duration_ms_is_not_aliased_to_agent_duration_ms(
    monkeypatch,
) -> None:
    """Regression guard: total_duration_ms must come from record.total_duration_ms,
    not record.agent_duration_ms. The alias bug at validator.py was fixed in T6
    and must stay fixed.
    """
    captured: list[dict[str, Any]] = []

    def fake_publish(
        event_type: str,
        payload: dict[str, Any],
        *,
        component: str = "sidequest-server",  # noqa: ARG001
        severity: str = "info",  # noqa: ARG001
    ) -> None:
        if event_type == "turn_complete":
            captured.append(payload)

    monkeypatch.setattr(
        "sidequest.telemetry.validator.publish_event",
        fake_publish,
    )

    v = Validator()
    v._checks = []  # noqa: SLF001 — see sibling test
    record = _make_record(agent_duration_ms=14336, total_duration_ms=101000)
    await v._validate(record)  # noqa: SLF001

    payload = captured[0]
    assert payload["agent_duration_ms"] == 14336
    assert payload["total_duration_ms"] == 101000
    assert payload["agent_duration_ms"] != payload["total_duration_ms"]
