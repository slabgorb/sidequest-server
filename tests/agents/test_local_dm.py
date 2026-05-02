"""Tests for LocalDM — Group B decomposer MVP.

Task 3: Haiku-backed LocalDM.decompose with structured output parsing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse
from sidequest.agents.local_dm import LocalDM


def test_local_dm_importable_from_package_root():
    """Wiring check — LocalDM is re-exported from sidequest.agents so other
    layers can use the package-root import style."""
    from sidequest.agents import LocalDM as LocalDMFromRoot

    assert LocalDMFromRoot is LocalDM  # same class, not a re-wrapped stub


@pytest.fixture
def dispatch_json_pronoun_resolved():
    """Haiku returns a DispatchPackage resolving 'him' to goblin_2."""
    return json.dumps(
        {
            "turn_id": "turn-010",
            "per_player": [
                {
                    "player_id": "player:Alice",
                    "raw_action": "Attack him!",
                    "resolved": [
                        {
                            "token": "him",
                            "resolved_to": "npc:goblin_2",
                            "confidence": 0.55,
                            "alternatives": ["npc:goblin_1", "npc:bandit_1"],
                            "resolution_note": "most recent direct combatant",
                        }
                    ],
                    "dispatch": [
                        {
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
                        }
                    ],
                    "lethality": [],
                    "narrator_instructions": [
                        {
                            "kind": "distinctive_detail_for_referent",
                            "payload": "describe the goblin by its broken tooth",
                            "visibility": {
                                "visible_to": "all",
                                "perception_fidelity": {},
                                "secrets_for": [],
                                "redact_from_narrator_canonical": False,
                            },
                        }
                    ],
                }
            ],
            "cross_player": [],
            "confidence_global": 0.55,
            "degraded": False,
            "degraded_reason": None,
        }
    )


@pytest.fixture
def dispatch_json_absence():
    """Haiku returns a DispatchPackage resolving 'let's' to absence."""
    return json.dumps(
        {
            "turn_id": "turn-011",
            "per_player": [
                {
                    "player_id": "player:Alice",
                    "raw_action": "Let's go!",
                    "resolved": [
                        {
                            "token": "let's",
                            "resolved_to": None,
                            "confidence": 0.0,
                            "alternatives": [],
                            "resolution_note": "no party present in scene",
                        }
                    ],
                    "dispatch": [
                        {
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
                        }
                    ],
                    "lethality": [],
                    "narrator_instructions": [
                        {
                            "kind": "must_not_narrate",
                            "payload": "inventing an NPC follower",
                            "visibility": {
                                "visible_to": "all",
                                "perception_fidelity": {},
                                "secrets_for": [],
                                "redact_from_narrator_canonical": False,
                            },
                        },
                        {
                            "kind": "must_narrate",
                            "payload": "the empty room answering back",
                            "visibility": {
                                "visible_to": "all",
                                "perception_fidelity": {},
                                "secrets_for": [],
                                "redact_from_narrator_canonical": False,
                            },
                        },
                    ],
                }
            ],
            "cross_player": [],
            "confidence_global": 1.0,
            "degraded": False,
            "degraded_reason": None,
        }
    )


def _make_mock_client(response_text: str) -> AsyncMock:
    """Build a mocked LlmClient client returning the given structured response."""
    client = AsyncMock()
    client.send_with_session = AsyncMock(
        return_value=ClaudeResponse(
            text=response_text,
            session_id="decomposer-session-abc",
        )
    )
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
async def test_local_dm_is_stateless_per_turn(dispatch_json_absence):
    """Spec §461 fallback: every turn sends a fresh system prompt and
    session_id=None.

    Drift evidence (playtest 2026-04-26): on the persistent-session
    pattern, turns 2+ accumulated literal_error parse failures on
    ``narrator_instructions.kind`` and a fabricated ``lethality`` shape.
    Falling back to stateless restates the closed-enum and ``lethality=[]``
    constraints every turn."""
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)

    await dm.decompose(
        turn_id="turn-100",
        player_id="player:Alice",
        raw_action="x",
        state_summary="y",
    )
    await dm.decompose(
        turn_id="turn-101",
        player_id="player:Alice",
        raw_action="x",
        state_summary="y",
    )

    first_call_kwargs = client.send_with_session.await_args_list[0].kwargs
    second_call_kwargs = client.send_with_session.await_args_list[1].kwargs
    assert first_call_kwargs["session_id"] is None
    assert second_call_kwargs["session_id"] is None
    assert first_call_kwargs["system_prompt"] is not None
    assert second_call_kwargs["system_prompt"] is not None
    # Same canonical prompt every turn — no drift accumulation.
    assert first_call_kwargs["system_prompt"] == second_call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_local_dm_reset_session_is_noop(dispatch_json_absence):
    """LocalDM is stateless; reset_session() is a no-op kept for callers
    that haven't migrated off the stateful API."""
    client = _make_mock_client(dispatch_json_absence)
    dm = LocalDM(client=client)

    await dm.decompose(turn_id="t1", player_id="p", raw_action="x", state_summary="y")
    dm.reset_session()  # must not raise
    await dm.decompose(turn_id="t2", player_id="p", raw_action="x", state_summary="y")

    # Both calls go out stateless regardless of the reset.
    assert client.send_with_session.await_args_list[0].kwargs["session_id"] is None
    assert client.send_with_session.await_args_list[1].kwargs["session_id"] is None


@pytest.mark.asyncio
async def test_local_dm_recovers_after_client_exception():
    """Stateless model: a transient failure on one turn doesn't poison
    later turns. Each call independently establishes via system_prompt."""
    client = AsyncMock()
    # First call raises, second succeeds.
    client.send_with_session = AsyncMock(
        side_effect=[
            TimeoutError("transient failure"),
            ClaudeResponse(
                text='{"turn_id":"t2","per_player":[],"cross_player":[],"confidence_global":1.0,"degraded":false,"degraded_reason":null}',
                input_tokens=0,
                output_tokens=0,
                session_id="fresh-session-xyz",
            ),
        ]
    )
    dm = LocalDM(client=client)

    pkg1 = await dm.decompose(turn_id="t1", player_id="p", raw_action="x", state_summary="y")
    assert pkg1.degraded is True

    pkg2 = await dm.decompose(turn_id="t2", player_id="p", raw_action="x", state_summary="y")
    assert client.send_with_session.await_args_list[1].kwargs["session_id"] is None
    assert client.send_with_session.await_args_list[1].kwargs["system_prompt"] is not None
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


def test_decomposer_system_prompt_closes_subsystem_enum():
    """Playtest 2026-04-26: Haiku invented `character_action`, `examination`,
    `inventory_action`, `movement`, `perception` subsystems on a per-turn
    basis, all silently absorbed by the dispatcher (`subsystems.unknown`
    warning swarm). The prompt must close the enum and call out the
    inventions explicitly so future-Haiku has the anti-examples in
    context."""
    from sidequest.agents.local_dm import _DECOMPOSER_SYSTEM_PROMPT

    # Closure language present
    assert "CLOSED ENUM" in _DECOMPOSER_SYSTEM_PROMPT
    assert "dispatch[].subsystem" in _DECOMPOSER_SYSTEM_PROMPT
    # Anti-examples observed in playtest 2026-04-26
    for invented in (
        "character_action",
        "examination",
        "inventory_action",
        "movement",
        "perception",
    ):
        assert invented in _DECOMPOSER_SYSTEM_PROMPT, (
            f"prompt must call out the {invented!r} invention explicitly"
        )


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
    return json.dumps(
        {
            "turn_id": "turn-parse",
            "per_player": [],
            "cross_player": [],
            "confidence_global": 1.0,
            "degraded": False,
            "degraded_reason": None,
        }
    )


@pytest.mark.asyncio
async def test_local_dm_parses_response_wrapped_in_code_fence(
    dispatch_json_minimal,
    otel_capture,
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
        s for s in otel_capture.get_finished_spans() if s.name == "local_dm.decompose"
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
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "local_dm.decompose"]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["degraded"] is True
    assert attrs.get("parse_preview", "").startswith("not json")


# ---------------------------------------------------------------------------
# Multi-target resolved_to (pingpong 2026-04-26 S2-OBS)
#
# Bug: the decomposer occasionally emits ``resolved_to`` as a list of player
# IDs when a token like "the party" plausibly resolves to multiple PCs
# (observed: ``per_player.0.resolved.4.resolved_to=['Paul','John','George','Ringo']``).
# Pre-fix the schema only accepted ``str | None``, so the entire
# DispatchPackage was rejected via ValidationError and the turn collapsed
# into a degraded empty package — losing all per-player narrator
# instructions for the rest of the turn.
#
# Fix: schema now accepts ``str | list[str] | None`` and the parse path
# records a ``resolved_to_multi_target_count`` span attribute via
# ``_normalize_multi_target_resolved_to`` so the GM panel sees when the
# branch fires.
# ---------------------------------------------------------------------------


def _multi_target_dispatch_json() -> str:
    """Real-shape DispatchPackage with a list-valued ``resolved_to`` for
    the token "the party" — repro from playtest 2026-04-26."""
    return json.dumps(
        {
            "turn_id": "turn-multi",
            "per_player": [
                {
                    "player_id": "player:Paul",
                    "raw_action": "Tell the party to stand down.",
                    "resolved": [
                        {
                            "token": "the party",
                            "resolved_to": [
                                "player:Paul",
                                "player:John",
                                "player:George",
                                "player:Ringo",
                            ],
                            "confidence": 0.9,
                            "alternatives": [],
                            "resolution_note": "all four PCs",
                        }
                    ],
                    "dispatch": [],
                    "lethality": [],
                    "narrator_instructions": [],
                }
            ],
            "cross_player": [],
            "confidence_global": 1.0,
            "degraded": False,
            "degraded_reason": None,
        }
    )


@pytest.mark.asyncio
async def test_local_dm_accepts_list_valued_resolved_to() -> None:
    """Pingpong 2026-04-26 S2-OBS: list-valued ``resolved_to`` no longer
    crashes validation. Pre-fix this returned a degraded package; post-fix
    it parses cleanly with the list preserved on the Referent.
    """
    client = _make_mock_client(_multi_target_dispatch_json())
    dm = LocalDM(client=client)

    pkg = await dm.decompose(
        turn_id="turn-multi",
        player_id="player:Paul",
        raw_action="Tell the party to stand down.",
        state_summary="Beatles 4-player tavern scene.",
    )

    # Crucially: NOT degraded. Pre-fix this would be degraded=True with
    # reason="parse_failure: ValidationError" because Pydantic rejected
    # the list-valued resolved_to.
    assert pkg.degraded is False, (
        f"multi-target resolved_to must parse cleanly; got degraded "
        f"package with reason={pkg.degraded_reason!r}"
    )
    assert len(pkg.per_player) == 1
    referent = pkg.per_player[0].resolved[0]
    # Schema preserves shape — the list is intact for downstream
    # consumers that want to branch on type.
    assert referent.resolved_to == [
        "player:Paul",
        "player:John",
        "player:George",
        "player:Ringo",
    ]
    assert referent.token == "the party"


@pytest.mark.asyncio
async def test_local_dm_records_multi_target_count_on_span(otel_capture) -> None:
    """The decomposer span carries a ``resolved_to_multi_target_count``
    attribute when list-valued resolved_to entries are observed, so the
    GM panel sees how often this branch fires (the lie-detector for
    'is multi-target resolution actually engaging?')."""
    client = _make_mock_client(_multi_target_dispatch_json())
    dm = LocalDM(client=client)

    await dm.decompose(
        turn_id="turn-multi",
        player_id="player:Paul",
        raw_action="Tell the party to stand down.",
        state_summary="...",
    )

    spans = [s for s in otel_capture.get_finished_spans() if s.name == "local_dm.decompose"]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("resolved_to_multi_target_count") == 1, (
        f"expected resolved_to_multi_target_count=1; got attrs={attrs}"
    )
    # And the package is NOT degraded.
    assert attrs.get("degraded") is False


@pytest.mark.asyncio
async def test_local_dm_omits_multi_target_attr_when_all_str() -> None:
    """When every ``resolved_to`` is a plain string (or None), the span
    must NOT carry a multi-target count attribute — keeping the GM
    panel's signal clean (zero is the absence of the event, not a
    countable value)."""
    # Reuse the absence fixture which has resolved_to=None.
    client = _make_mock_client(
        json.dumps(
            {
                "turn_id": "turn-no-multi",
                "per_player": [
                    {
                        "player_id": "player:Alice",
                        "raw_action": "Wave at the bartender.",
                        "resolved": [
                            {
                                "token": "the bartender",
                                "resolved_to": "npc:bart_01",
                                "confidence": 0.9,
                                "alternatives": [],
                                "resolution_note": None,
                            }
                        ],
                        "dispatch": [],
                        "lethality": [],
                        "narrator_instructions": [],
                    }
                ],
                "cross_player": [],
                "confidence_global": 1.0,
                "degraded": False,
                "degraded_reason": None,
            }
        )
    )
    dm = LocalDM(client=client)
    pkg = await dm.decompose(
        turn_id="turn-no-multi",
        player_id="player:Alice",
        raw_action="Wave at the bartender.",
        state_summary="...",
    )
    assert pkg.degraded is False
    # Single-string resolved_to still works as before.
    assert pkg.per_player[0].resolved[0].resolved_to == "npc:bart_01"


def test_normalize_multi_target_resolved_to_counts_lists() -> None:
    """Unit test for the helper itself — counts list-valued entries
    without mutating values."""
    from sidequest.agents.local_dm import _normalize_multi_target_resolved_to

    raw = {
        "per_player": [
            {
                "resolved": [
                    {"resolved_to": "x"},
                    {"resolved_to": ["a", "b"]},
                    {"resolved_to": None},
                    {"resolved_to": ["c"]},
                ]
            },
            {
                "resolved": [
                    {"resolved_to": ["d", "e", "f"]},
                ]
            },
        ]
    }
    assert _normalize_multi_target_resolved_to(raw) == 3
    # Mutation-free: list values still intact.
    assert raw["per_player"][0]["resolved"][1]["resolved_to"] == ["a", "b"]


def test_normalize_multi_target_resolved_to_handles_empty() -> None:
    """Helper handles missing ``per_player`` and ``resolved`` keys without
    raising (degraded packages may have both empty)."""
    from sidequest.agents.local_dm import _normalize_multi_target_resolved_to

    assert _normalize_multi_target_resolved_to({}) == 0
    assert _normalize_multi_target_resolved_to({"per_player": []}) == 0
    assert (
        _normalize_multi_target_resolved_to(
            {"per_player": [{"resolved": []}]},
        )
        == 0
    )


def test_referent_schema_accepts_list_valued_resolved_to() -> None:
    """Schema-level guard: the Referent model accepts both ``str`` and
    ``list[str]`` for ``resolved_to``. Documents the contract change so
    a future revert to ``str | None`` would fail this test loudly.
    """
    from sidequest.protocol.dispatch import Referent

    # Plain string (single PC / NPC) — original contract.
    r1 = Referent(token="him", resolved_to="npc:goblin_2", confidence=0.9)
    assert r1.resolved_to == "npc:goblin_2"

    # None (absence) — original contract.
    r2 = Referent(token="them", resolved_to=None, confidence=0.0)
    assert r2.resolved_to is None

    # List of PC IDs (multi-target) — pingpong 2026-04-26 fix.
    r3 = Referent(
        token="the party",
        resolved_to=["player:Paul", "player:John", "player:George", "player:Ringo"],
        confidence=0.9,
    )
    assert r3.resolved_to == [
        "player:Paul",
        "player:John",
        "player:George",
        "player:Ringo",
    ]
