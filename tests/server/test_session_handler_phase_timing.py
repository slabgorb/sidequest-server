"""Wiring test: _execute_narration_turn populates all expected phase keys
in the resulting TurnRecord.

Lie-detector for "did we actually instrument all ten seams?" — a future
refactor that drops a `with timings.phase(...):` wrapper fails here.

Drives a single happy-path narration turn end-to-end through the real
:class:`Orchestrator` + real :class:`LocalDM` (each backed by the
autouse ``_FakeClaudeClient`` from ``conftest.py``) and asserts the
captured :class:`TurnRecord.phase_durations_ms` carries every expected
phase key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sidequest.agents.local_dm import LocalDM
from sidequest.agents.orchestrator import Orchestrator
from sidequest.genre.models.lethality import LethalityPolicy, VerdictsOnZeroEdge
from sidequest.telemetry.turn_record import TurnRecord
from tests.server.conftest import _build_turn_context_for_test

# Every phase ``with timings.phase(...)`` wrapper that fires on a happy-path
# narration turn. T4 wires the session-handler phases (preprocess_llm /
# state_apply / dispatch_post / broadcast / persistence); T5 wires the
# orchestrator phases (dispatch_bank / lethality_arbiter / prompt_build /
# narrator_subprocess / narrator_extraction). If a future refactor drops
# any wrapper, this set's `missing` assertion names the offender.
EXPECTED_PHASES: set[str] = {
    "preprocess_llm",
    "dispatch_bank",
    "lethality_arbiter",
    "prompt_build",
    "narrator_subprocess",
    "narrator_extraction",
    "state_apply",
    "dispatch_post",
    "broadcast",
    "persistence",
}


def _make_lethality_policy() -> LethalityPolicy:
    """Minimal valid policy so the orchestrator runs the lethality_arbiter
    phase. Without it, that ``with timings.phase("lethality_arbiter")``
    block is gated and the wiring assertion can't tell whether the wrapper
    is missing or merely skipped.
    """
    return LethalityPolicy(
        genre_key="caverns_and_claudes",
        default_reversibility="permanent",
        verdicts_on_zero_edge=VerdictsOnZeroEdge(
            pc="defeated",
            npc="defeated",
        ),
        soul_md_constraint="Honor the player's agency.",
        must_narrate="the consequence in the fiction",
        must_not_narrate="dice math",
    )


@pytest.mark.asyncio
async def test_execute_narration_turn_records_all_named_phases(
    session_fixture,
) -> None:
    sd, handler = session_fixture

    # Real Orchestrator + LocalDM, each backed by the autouse FakeClaudeClient
    # patched at orchestrator.ClaudeClient / local_dm.ClaudeClient (see
    # tests/server/conftest.py). A MagicMock orchestrator would short out the
    # five orchestrator-side phase wrappers we need to verify.
    sd.orchestrator = Orchestrator()
    sd.local_dm = LocalDM()

    # Capture the TurnRecord without running real validation checks.
    captured: list[TurnRecord] = []
    mock_validator = MagicMock()

    async def _capture(record: TurnRecord) -> None:
        captured.append(record)

    mock_validator.submit = AsyncMock(side_effect=_capture)
    mock_validator.is_running = MagicMock(return_value=True)
    handler._validator = mock_validator

    # Build a TurnContext and inject the lethality policy so the
    # ``lethality_arbiter`` phase wrapper actually executes (its block is
    # gated on ``context.lethality_policy is not None``).
    turn_context = _build_turn_context_for_test(sd)
    turn_context.lethality_policy = _make_lethality_policy()

    await handler._execute_narration_turn(  # noqa: SLF001 — direct test seam
        sd,
        "I look around carefully.",
        turn_context,
    )

    assert captured, "validator received no TurnRecord"
    record = captured[-1]

    present = set(record.phase_durations_ms.keys())
    missing = EXPECTED_PHASES - present
    assert not missing, (
        f"phase_timings missing expected keys: {sorted(missing)}; got: {sorted(present)}"
    )

    # Every recorded phase must also be reflected in phase_call_counts —
    # the two dicts ship together through the validator and must agree.
    assert set(record.phase_call_counts.keys()) >= EXPECTED_PHASES, (
        "phase_call_counts missing keys present in phase_durations_ms"
    )

    # Total wall-clock must be positive and at least cover the agent
    # (narrator subprocess) duration. Zero would mean ``mark_done`` never
    # ran or the action_received_monotonic seed wasn't set.
    assert record.total_duration_ms > 0
    assert record.total_duration_ms >= record.agent_duration_ms
