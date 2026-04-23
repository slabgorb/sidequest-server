"""Task 11: CONFRONTATION message dispatched on encounter begin/active/end.

These tests mock the orchestrator — no LLM call — and assert the handler
pushes a single ConfrontationMessage into the outbound list per transition.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sidequest.agents.orchestrator import BeatSelection, NarrationTurnResult
from sidequest.protocol.messages import ConfrontationMessage


def _result(narration: str = "ok", **kwargs) -> NarrationTurnResult:
    return NarrationTurnResult(narration=narration, **kwargs)


@pytest.mark.asyncio
async def test_confrontation_message_emitted_on_encounter_start(
    session_handler_factory,
):
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(confrontation="combat"),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "I attack the goblins!", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    assert conf[0].payload.active is True
    assert conf[0].payload.type == "combat"
    assert [b["id"] for b in conf[0].payload.beats]  # beats included


@pytest.mark.asyncio
async def test_confrontation_message_active_false_when_resolved(
    session_handler_factory,
):
    from sidequest.game.encounter import (
        EncounterMetric,
        MetricDirection,
        StructuredEncounter,
    )
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    enc = StructuredEncounter.combat(combatants=["Rux"], hp=10)
    enc.metric = EncounterMetric(
        name="momentum", current=9, starting=0,
        direction=MetricDirection.Bidirectional,
        threshold_high=10, threshold_low=-10,
    )
    sd.snapshot.encounter = enc
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(
            beat_selections=[BeatSelection(actor="Rux", beat_id="attack", target=None)],
        ),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Press the attack!", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 1
    assert conf[0].payload.active is False


@pytest.mark.asyncio
async def test_no_confrontation_message_when_state_unchanged(
    session_handler_factory,
):
    """No encounter before and no encounter after → no CONFRONTATION message."""
    sd, handler = session_handler_factory(genre="caverns_and_claudes")
    sd.orchestrator.run_narration_turn = AsyncMock(
        return_value=_result(narration="You take a quiet walk."),
    )
    from sidequest.server.session_handler import _build_turn_context
    msgs = await handler._execute_narration_turn(
        sd, "Walk quietly.", _build_turn_context(sd),
    )
    conf = [m for m in msgs if isinstance(m, ConfrontationMessage)]
    assert len(conf) == 0
