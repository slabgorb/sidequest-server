"""Wiring tests — LocalDM runs between sealed-letter and narrator in the session handler."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server.conftest import _build_turn_context_for_test, _make_minimal_narration_turn_result

# ---------------------------------------------------------------------------
# Module-scoped JSON fixtures (copied verbatim from tests/agents/test_local_dm.py
# so server integration tests are self-contained without reaching into a
# peer test module's pytest fixtures).
# ---------------------------------------------------------------------------

PRONOUN_RESOLVED_JSON = json.dumps({
    "turn_id": "turn-010",
    "per_player": [{
        "player_id": "player:Alice",
        "raw_action": "Attack him!",
        "resolved": [{
            "token": "him",
            "resolved_to": "npc:goblin_2",
            "confidence": 0.55,
            "alternatives": ["npc:goblin_1", "npc:bandit_1"],
            "resolution_note": "most recent direct combatant",
        }],
        "dispatch": [{
            "subsystem": "distinctive_detail_hint",
            "params": {"target": "npc:goblin_2", "hint": "broken tooth"},
            "depends_on": [],
            "idempotency_key": "idem:turn-010:alice:0",
            "visibility": {
                "visible_to": "all",
                "perception_fidelity": {},
                "secrets_for": [],
                "redact_from_narrator_canonical": False,
            },
        }],
        "lethality": [],
        "narrator_instructions": [{
            "kind": "distinctive_detail_for_referent",
            "payload": "describe the goblin by its broken tooth",
            "visibility": {
                "visible_to": "all",
                "perception_fidelity": {},
                "secrets_for": [],
                "redact_from_narrator_canonical": False,
            },
        }],
    }],
    "cross_player": [],
    "confidence_global": 0.55,
    "degraded": False,
    "degraded_reason": None,
})


ABSENCE_JSON = json.dumps({
    "turn_id": "turn-011",
    "per_player": [{
        "player_id": "player:Alice",
        "raw_action": "Let's go!",
        "resolved": [{
            "token": "let's",
            "resolved_to": None,
            "confidence": 0.0,
            "alternatives": [],
            "resolution_note": "no party present in scene",
        }],
        "dispatch": [{
            "subsystem": "reflect_absence",
            "params": {"addressee_hint": "no party"},
            "depends_on": [],
            "idempotency_key": "idem:turn-011:alice:0",
            "visibility": {
                "visible_to": "all",
                "perception_fidelity": {},
                "secrets_for": [],
                "redact_from_narrator_canonical": False,
            },
        }],
        "lethality": [],
        "narrator_instructions": [{
            "kind": "must_not_narrate",
            "payload": "inventing an NPC follower",
            "visibility": {
                "visible_to": "all",
                "perception_fidelity": {},
                "secrets_for": [],
                "redact_from_narrator_canonical": False,
            },
        }, {
            "kind": "must_narrate",
            "payload": "the empty room answering back",
            "visibility": {
                "visible_to": "all",
                "perception_fidelity": {},
                "secrets_for": [],
                "redact_from_narrator_canonical": False,
            },
        }],
    }],
    "cross_player": [],
    "confidence_global": 1.0,
    "degraded": False,
    "degraded_reason": None,
})


def _install_real_orchestrator(sd) -> None:
    """Replace ``sd.orchestrator`` (MagicMock by default in session_fixture)
    with a real :class:`Orchestrator` wired to a MagicMock ClaudeLike client.

    Integration tests that need the full narrator-prompt pipeline
    (``build_narrator_prompt`` + directive injection from the dispatch bank)
    must execute real production code — a MagicMock orchestrator would short
    out the wiring we are trying to verify.
    """
    from sidequest.agents.claude_client import ClaudeClient
    from sidequest.agents.orchestrator import Orchestrator

    sd.orchestrator = Orchestrator(client=MagicMock(spec=ClaudeClient))


async def test_execute_narration_turn_invokes_local_dm_before_narrator(session_fixture):
    """The session handler calls LocalDM.decompose exactly once before
    orchestrator.run_narration_turn, and attaches the result to TurnContext."""
    sd, handler = session_fixture

    captured: dict = {}
    call_order: list[str] = []

    async def fake_decompose(**kwargs):
        from sidequest.protocol.dispatch import DispatchPackage
        call_order.append("decompose")
        captured["decomposer_called"] = True
        captured["raw_action"] = kwargs["raw_action"]
        return DispatchPackage(
            turn_id=kwargs["turn_id"], per_player=[], cross_player=[],
            confidence_global=1.0, degraded=False, degraded_reason=None,
        )

    async def fake_run_narration_turn(action, context):
        call_order.append("narrator")
        captured["narrator_called"] = True
        captured["narrator_saw_dispatch_package"] = context.dispatch_package is not None
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(sd.local_dm, "decompose", side_effect=fake_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock(side_effect=fake_run_narration_turn)):
        await handler._execute_narration_turn(sd, "I look around.", _build_turn_context_for_test(sd))

    assert captured["decomposer_called"] is True
    assert captured["narrator_called"] is True
    assert captured["raw_action"] == "I look around."
    assert captured["narrator_saw_dispatch_package"] is True
    # decomposer must run before the narrator
    assert call_order == ["decompose", "narrator"], f"Expected decompose→narrator, got {call_order}"


async def test_execute_narration_turn_continues_when_decomposer_degraded(session_fixture):
    """A degraded decomposer package does not abort the turn."""
    sd, handler = session_fixture

    async def degraded_decompose(**kwargs):
        from sidequest.protocol.dispatch import DispatchPackage
        return DispatchPackage(
            turn_id=kwargs["turn_id"], per_player=[], cross_player=[],
            confidence_global=0.0, degraded=True, degraded_reason="test-forced",
        )

    narrator_called = False

    async def fake_run(action, context):
        nonlocal narrator_called
        narrator_called = True
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(sd.local_dm, "decompose", side_effect=degraded_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock(side_effect=fake_run)):
        await handler._execute_narration_turn(sd, "x", _build_turn_context_for_test(sd))

    assert narrator_called, "narrator must still run when decomposer is degraded"


async def test_execute_narration_turn_propagates_programmer_bug_exceptions(session_fixture):
    """Exceptions escaping LocalDM.decompose indicate programmer bugs
    (rename, signature drift). The session handler must NOT swallow them —
    LocalDM already converts expected failures to degraded packages internally.
    """
    sd, handler = session_fixture

    async def buggy_decompose(**kwargs):
        # Simulate a programmer bug — e.g. AttributeError from a rename.
        raise AttributeError("simulated programmer bug")

    with patch.object(sd.local_dm, "decompose", side_effect=buggy_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock()), \
         pytest.raises(AttributeError, match="simulated programmer bug"):
        await handler._execute_narration_turn(sd, "x", _build_turn_context_for_test(sd))


async def test_execute_narration_turn_turn_id_includes_player_id(session_fixture):
    """turn_id must include player_id to disambiguate concurrent sessions in
    the same genre:world at the same interaction number."""
    sd, handler = session_fixture

    captured: dict = {}

    async def fake_decompose(**kwargs):
        from sidequest.protocol.dispatch import DispatchPackage
        captured["turn_id"] = kwargs["turn_id"]
        return DispatchPackage(
            turn_id=kwargs["turn_id"], per_player=[], cross_player=[],
            confidence_global=1.0, degraded=False, degraded_reason=None,
        )

    async def fake_run(action, context):
        return _make_minimal_narration_turn_result(narration="ok")

    with patch.object(sd.local_dm, "decompose", side_effect=fake_decompose), \
         patch.object(sd.orchestrator, "run_narration_turn", AsyncMock(side_effect=fake_run)):
        await handler._execute_narration_turn(sd, "x", _build_turn_context_for_test(sd))

    assert sd.player_id in captured["turn_id"], (
        f"turn_id={captured['turn_id']!r} must include player_id={sd.player_id!r}"
    )
    assert captured["turn_id"].startswith(f"{sd.genre_slug}:{sd.world_slug}:{sd.player_id}:")


# ---------------------------------------------------------------------------
# Task 12 — end-to-end happy path: pronoun resolved, directive reaches prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_turn_happy_path_pronoun_resolved(session_fixture):
    """Decomposer returns a clean pronoun resolution; the narrator prompt
    carries the distinctive_detail directive; no degraded flag."""
    sd, handler = session_fixture
    _install_real_orchestrator(sd)

    from sidequest.agents.claude_client import ClaudeResponse

    sd.local_dm._client = AsyncMock()
    sd.local_dm._client.send_with_session = AsyncMock(return_value=ClaudeResponse(
        text=PRONOUN_RESOLVED_JSON, session_id="dec-sess-xyz",
    ))

    captured_prompt: dict = {}
    orig_build = sd.orchestrator.build_narrator_prompt

    async def spying_build(action, context, *, tier):
        prompt_text, registry = await orig_build(action, context, tier=tier)
        captured_prompt["text"] = prompt_text
        return prompt_text, registry

    sd.orchestrator.build_narrator_prompt = spying_build

    sd.orchestrator._client = AsyncMock()
    sd.orchestrator._client.send_with_session = AsyncMock(return_value=ClaudeResponse(
        text='{"narration": "ok"}', session_id="n",
    ))

    await handler._execute_narration_turn(sd, "Attack him!", _build_turn_context_for_test(sd))

    assert "text" in captured_prompt, "narrator prompt was never built"
    prompt_text = captured_prompt["text"]
    # The distinctive_detail_hint subsystem should have emitted a directive.
    assert "distinctive_detail_for_referent" in prompt_text or "broken tooth" in prompt_text, (
        f"expected distinctive_detail directive in prompt; got: {prompt_text[:500]}"
    )
