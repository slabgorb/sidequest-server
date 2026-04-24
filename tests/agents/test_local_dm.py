"""Tests for LocalDM — Group B decomposer MVP.

Task 3: Haiku-backed LocalDM.decompose with structured output parsing.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.local_dm import LocalDM
from sidequest.protocol.dispatch import DispatchPackage


def test_local_dm_importable_from_package_root():
    """Wiring check — LocalDM is re-exported from sidequest.agents so other
    layers can use the package-root import style."""
    from sidequest.agents import LocalDM as LocalDMFromRoot
    assert LocalDMFromRoot is LocalDM  # same class, not a re-wrapped stub


@pytest.fixture
def dispatch_json_pronoun_resolved():
    """Haiku returns a DispatchPackage resolving 'him' to goblin_2."""
    return json.dumps({
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


@pytest.fixture
def dispatch_json_absence():
    """Haiku returns a DispatchPackage resolving 'let's' to absence."""
    return json.dumps({
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


def _make_mock_client(response_text: str) -> AsyncMock:
    """Build a mocked LlmClient client returning the given structured response."""
    client = AsyncMock()
    client.send_with_session = AsyncMock(return_value=ClaudeResponse(
        text=response_text,
        session_id="decomposer-session-abc",
    ))
    return client


@pytest.mark.asyncio
async def test_local_dm_resolves_pronoun_via_haiku(dispatch_json_pronoun_resolved):
    client = _make_mock_client(dispatch_json_pronoun_resolved)
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-010",
        player_id="player:Alice",
        raw_action="Attack him!",
        state_summary="Goblins 1-3 and a bandit are in the room.",
    )

    assert pkg.degraded is False
    assert len(pkg.per_player) == 1
    dispatch = pkg.per_player[0]
    assert dispatch.resolved[0].resolved_to == "npc:goblin_2"
    assert "npc:goblin_1" in dispatch.resolved[0].alternatives
    # At least one call was made.
    assert client.send_with_session.await_count == 1


@pytest.mark.asyncio
async def test_local_dm_handles_absence(dispatch_json_absence):
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-011",
        player_id="player:Alice",
        raw_action="Let's go!",
        state_summary="You are alone in the tavern.",
    )

    assert pkg.per_player[0].resolved[0].resolved_to is None
    # Directives include both must_not and must — the absence response shape.
    kinds = [d.kind for d in pkg.per_player[0].narrator_instructions]
    assert "must_not_narrate" in kinds
    assert "must_narrate" in kinds


@pytest.mark.asyncio
async def test_local_dm_degraded_on_parse_failure():
    """If Haiku returns unparsable JSON, decomposer returns degraded package (spec §6.6)."""
    client = _make_mock_client("not json at all")
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-err",
        player_id="player:Alice",
        raw_action="anything",
        state_summary="...",
    )
    assert pkg.degraded is True
    assert pkg.degraded_reason
    assert pkg.turn_id == "turn-err"
    assert pkg.per_player == []


@pytest.mark.asyncio
async def test_local_dm_degraded_on_client_exception():
    """Client timeout / network error → degraded package, no crash."""
    client = AsyncMock()
    client.send_with_session = AsyncMock(side_effect=TimeoutError("decomposer timeout"))
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-timeout",
        player_id="player:Alice",
        raw_action="whatever",
        state_summary="...",
    )
    assert pkg.degraded is True
    assert "timeout" in pkg.degraded_reason.lower()


@pytest.mark.asyncio
async def test_local_dm_persistent_session_resumes_on_second_call(dispatch_json_absence):
    """ADR-066 — first call establishes session; second call resumes."""
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)

    await dm.decompose(
        turn_id="turn-100", player_id="player:Alice",
        raw_action="x", state_summary="y",
    )
    await dm.decompose(
        turn_id="turn-101", player_id="player:Alice",
        raw_action="x", state_summary="y",
    )

    first_call_kwargs = client.send_with_session.await_args_list[0].kwargs
    second_call_kwargs = client.send_with_session.await_args_list[1].kwargs
    assert first_call_kwargs["session_id"] is None
    assert second_call_kwargs["session_id"] == "decomposer-session-abc"


@pytest.mark.asyncio
async def test_local_dm_reset_session_clears_id(dispatch_json_absence):
    """Reset returns subsequent calls to first-turn (session_id=None)."""
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)

    await dm.decompose(turn_id="t1", player_id="p", raw_action="x", state_summary="y")
    dm.reset_session()
    await dm.decompose(turn_id="t2", player_id="p", raw_action="x", state_summary="y")

    assert client.send_with_session.await_args_list[1].kwargs["session_id"] is None


@pytest.mark.asyncio
async def test_local_dm_resets_session_on_client_exception():
    """Stale-session guard: after a client exception, the next call must
    establish a fresh session (session_id=None) rather than retrying the
    possibly-stale cached id."""
    client = AsyncMock()
    # First call raises.
    client.send_with_session = AsyncMock(side_effect=[
        TimeoutError("transient failure"),
        ClaudeResponse(
            text='{"turn_id":"t2","per_player":[],"cross_player":[],"confidence_global":1.0,"degraded":false,"degraded_reason":null}',
            input_tokens=0,
            output_tokens=0,
            session_id="fresh-session-xyz",
        ),
    ])
    dm = LocalDM(client=client)

    pkg1 = await dm.decompose(turn_id="t1", player_id="p", raw_action="x", state_summary="y")
    assert pkg1.degraded is True

    # Second call must go out with session_id=None (fresh start).
    pkg2 = await dm.decompose(turn_id="t2", player_id="p", raw_action="x", state_summary="y")
    assert client.send_with_session.await_args_list[1].kwargs["session_id"] is None
    assert pkg2.degraded is False


@pytest.mark.asyncio
async def test_local_dm_emits_decompose_span(dispatch_json_absence, otel_capture):
    """LocalDM.decompose emits a local_dm.decompose span with turn/player/
    action_len attrs and a degraded flag on every call — the GM panel lie
    detector for whether the decomposer actually ran this turn."""
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)
    await dm.decompose(
        turn_id="turn-span",
        player_id="player:Alice",
        raw_action="test action",
        state_summary="state",
    )
    spans = otel_capture.get_finished_spans()
    names = [s.name for s in spans]
    assert "local_dm.decompose" in names

    decompose_spans = [s for s in spans if s.name == "local_dm.decompose"]
    assert len(decompose_spans) == 1
    attrs = dict(decompose_spans[0].attributes or {})
    assert attrs["turn_id"] == "turn-span"
    assert attrs["player_id"] == "player:Alice"
    assert attrs["action_len"] == len("test action")
    assert "degraded" in attrs
    # Clean parse path from the absence fixture → degraded=False.
    assert attrs["degraded"] is False


@pytest.mark.asyncio
async def test_local_dm_decompose_span_records_degraded_reason(otel_capture):
    """Failure paths (parse failure here) must set degraded=True and
    degraded_reason on the span so the GM panel distinguishes degraded
    turns from clean ones."""
    client = _make_mock_client("not json at all")
    dm = LocalDM(client=client)
    await dm.decompose(
        turn_id="turn-bad",
        player_id="player:Alice",
        raw_action="x",
        state_summary="y",
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "local_dm.decompose"]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["degraded"] is True
    assert "parse_failure" in attrs["degraded_reason"]


def test_decomposer_system_prompt_includes_schema_shape():
    """Haiku needs the DispatchPackage field names to produce parseable output."""
    from sidequest.agents.local_dm import _DECOMPOSER_SYSTEM_PROMPT
    # Top-level fields
    for name in ("turn_id", "per_player", "cross_player", "confidence_global", "degraded"):
        assert name in _DECOMPOSER_SYSTEM_PROMPT
    # Known subsystems
    for sub in ("reflect_absence", "distinctive_detail_hint", "npc_agency"):
        assert sub in _DECOMPOSER_SYSTEM_PROMPT
    # Directive kinds
    for kind in ("must_narrate", "must_not_narrate", "distinctive_detail_for_referent"):
        assert kind in _DECOMPOSER_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# JSON extraction — regression for playtest 2026-04-24
#
# Bug: Haiku returned responses wrapped in ```json ... ``` fences (and/or with
# preamble/trailing prose). `json.loads` blew up with JSONDecodeError on every
# turn, leaving dispatch_count=0 and burning 45s on the critical path.
#
# Fix: `_extract_json_object` strips fences + prose before the parse, and
# records every peel as an OTEL span attribute so the GM panel can see when
# Haiku is violating the "JSON only" contract.
# ---------------------------------------------------------------------------


def test_extract_json_object_passes_through_clean_json():
    from sidequest.agents.local_dm import _extract_json_object

    cleaned, steps = _extract_json_object('{"a": 1, "b": "c"}')
    assert cleaned == '{"a": 1, "b": "c"}'
    assert steps == []


def test_extract_json_object_strips_code_fence():
    from sidequest.agents.local_dm import _extract_json_object

    raw = '```json\n{"a": 1}\n```'
    cleaned, steps = _extract_json_object(raw)
    assert cleaned == '{"a": 1}'
    assert "strip_fence" in steps


def test_extract_json_object_strips_preamble_and_trailing():
    from sidequest.agents.local_dm import _extract_json_object

    raw = 'Here is the DispatchPackage:\n{"a": 1}\n\nHope that helps!'
    cleaned, steps = _extract_json_object(raw)
    assert cleaned == '{"a": 1}'
    assert "strip_preamble" in steps
    assert "strip_trailing" in steps


def test_extract_json_object_honors_braces_inside_strings():
    from sidequest.agents.local_dm import _extract_json_object

    # A `}` inside a quoted string value must NOT end the balanced-brace scan
    # — otherwise the extractor would truncate mid-object.
    raw = '{"msg": "a } inside a string", "n": 7}'
    cleaned, _ = _extract_json_object(raw)
    assert cleaned == raw


@pytest.fixture
def dispatch_json_minimal():
    """Smallest valid DispatchPackage — used as the body inside wrappers."""
    return json.dumps({
        "turn_id": "turn-parse",
        "per_player": [],
        "cross_player": [],
        "confidence_global": 1.0,
        "degraded": False,
        "degraded_reason": None,
    })


@pytest.mark.asyncio
async def test_local_dm_parses_response_wrapped_in_code_fence(
    dispatch_json_minimal, otel_capture,
):
    """Regression wiring test — production Haiku output (Playtest 2026-04-24
    Bug 3) arrived wrapped in a ```json ... ``` fence, which `json.loads`
    rejected with JSONDecodeError → every turn degraded, dispatch_count=0.

    The decomposer must strip the fence and parse cleanly. The OTEL span
    must record ``json_cleanup_steps=strip_fence`` so the GM panel can see
    the contract is being violated even though the turn recovered."""
    fenced = f"```json\n{dispatch_json_minimal}\n```"
    client = _make_mock_client(fenced)
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-parse",
        player_id="player:Alice",
        raw_action="look around",
        state_summary="...",
    )

    # Clean parse — no degradation.
    assert pkg.degraded is False, (
        f"fenced response should parse cleanly, got degraded: {pkg.degraded_reason}"
    )
    assert pkg.turn_id == "turn-parse"

    # OTEL contract: cleanup was recorded so the GM panel sees it.
    decompose_spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "local_dm.decompose"
    ]
    assert len(decompose_spans) == 1
    attrs = dict(decompose_spans[0].attributes or {})
    assert "json_cleanup_steps" in attrs
    assert "strip_fence" in attrs["json_cleanup_steps"]


@pytest.mark.asyncio
async def test_local_dm_parses_response_with_preamble(dispatch_json_minimal):
    """Preamble prose before the JSON object must not break the parse."""
    wrapped = f"Here is the DispatchPackage:\n{dispatch_json_minimal}"
    client = _make_mock_client(wrapped)
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-parse",
        player_id="player:Alice",
        raw_action="x",
        state_summary="y",
    )
    assert pkg.degraded is False
    assert pkg.turn_id == "turn-parse"


@pytest.mark.asyncio
async def test_local_dm_parse_failure_records_preview_on_span(otel_capture):
    """When extraction still can't salvage the response, the span must
    expose a short ``parse_preview`` attribute so the GM panel can see
    what Haiku actually returned instead of guessing from a
    JSONDecodeError classname."""
    client = _make_mock_client("not json at all, just chatter")
    dm = LocalDM(client=client)
    await dm.decompose(
        turn_id="turn-bad",
        player_id="player:Alice",
        raw_action="x",
        state_summary="y",
    )
    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "local_dm.decompose"
    ]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["degraded"] is True
    assert attrs.get("parse_preview", "").startswith("not json")
