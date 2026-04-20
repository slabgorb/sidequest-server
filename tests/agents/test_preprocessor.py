"""Tests for preprocessor.py — PlayerActionPreprocessor.

Port of sidequest-agents/src/preprocessor.rs inline tests plus
additional coverage. No live LLM calls — ClaudeClient is mocked
via spawn_fn.
"""

from __future__ import annotations

import json

import pytest

from sidequest.agents import (
    ClaudeClient,
    ClaudeResponse,
    LlmFailed,
    OutputTooLong,
    ParseFailed,
    build_prompt,
    parse_response,
    preprocess_action_with_client,
)
from sidequest.agents.preprocessor import preprocess_action_with_client
from sidequest.game.turn import PreprocessedAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_preprocessed_json(
    you: str = "You draw your sword",
    named: str = "Kael draws their sword",
    intent: str = "draw sword",
    is_power_grab: bool = False,
    references_inventory: bool = False,
    references_npc: bool = False,
    references_ability: bool = False,
    references_location: bool = False,
) -> str:
    return json.dumps(
        {
            "you": you,
            "named": named,
            "intent": intent,
            "is_power_grab": is_power_grab,
            "references_inventory": references_inventory,
            "references_npc": references_npc,
            "references_ability": references_ability,
            "references_location": references_location,
        }
    )


class FixedResponseClient:
    """Mock ClaudeLike that returns a fixed response text."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    async def send_with_model(self, prompt: str, model: str) -> ClaudeResponse:
        return ClaudeResponse(text=self._response_text)

    async def send_with_session(self, *args, **kwargs) -> ClaudeResponse:  # type: ignore[override]
        return ClaudeResponse(text=self._response_text)


class FailingClient:
    """Mock ClaudeLike that always raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def send_with_model(self, prompt: str, model: str) -> ClaudeResponse:
        raise self._exc

    async def send_with_session(self, *args, **kwargs) -> ClaudeResponse:  # type: ignore[override]
        raise self._exc


# =========================================================================
# parse_response tests (ports Rust inline tests verbatim)
# =========================================================================


def test_parse_response_direct_json():
    json_str = json.dumps(
        {
            "you": "You draw your sword",
            "named": "Kael draws their sword",
            "intent": "draw sword",
            "is_power_grab": False,
            "references_inventory": False,
            "references_npc": False,
            "references_ability": False,
            "references_location": False,
        }
    )
    result = parse_response(json_str)
    assert result is not None
    assert result.you == "You draw your sword"
    assert result.named == "Kael draws their sword"
    assert result.intent == "draw sword"


def test_parse_response_with_markdown():
    payload = make_preprocessed_json(you="You look", named="Kael looks", intent="look")
    response = f"Here is the result:\n```json\n{payload}\n```"
    result = parse_response(response)
    assert result is not None
    assert result.you == "You look"


def test_parse_response_garbage():
    assert parse_response("not json at all") is None


def test_parse_response_minimal_fields():
    """PreprocessedAction should accept minimal JSON (defaults for booleans)."""
    payload = json.dumps({"you": "You look", "named": "Kael looks", "intent": "look"})
    result = parse_response(payload)
    assert result is not None
    assert result.is_power_grab is False
    assert result.references_inventory is False


def test_parse_response_with_booleans():
    payload = make_preprocessed_json(
        you="You wish for gold",
        named="Kael wishes for gold",
        intent="wish for gold",
        is_power_grab=True,
        references_location=True,
    )
    result = parse_response(payload)
    assert result is not None
    assert result.is_power_grab is True
    assert result.references_location is True


# =========================================================================
# build_prompt tests (port of Rust test)
# =========================================================================


def test_build_prompt_contains_key_elements():
    prompt = build_prompt("uh I like draw my sword", "Kael")
    assert "Kael" in prompt
    assert "uh I like draw my sword" in prompt
    assert '"you"' in prompt
    assert '"named"' in prompt
    assert '"intent"' in prompt


def test_build_prompt_contains_char_name_twice():
    """char_name appears in example output as well as input."""
    prompt = build_prompt("I attack", "Rux")
    assert prompt.count("Rux") >= 2


def test_build_prompt_all_eight_keys_mentioned():
    prompt = build_prompt("I go north", "Alex")
    for key in [
        '"you"',
        '"named"',
        '"intent"',
        '"is_power_grab"',
        '"references_inventory"',
        '"references_npc"',
        '"references_ability"',
        '"references_location"',
    ]:
        assert key in prompt, f"Missing key {key} in prompt"


# =========================================================================
# preprocess_action_with_client tests
# =========================================================================


@pytest.mark.asyncio
async def test_preprocess_action_with_client_success():
    payload = make_preprocessed_json(
        you="You draw your sword",
        named="Kael draws their sword",
        intent="draw sword",
    )
    client = FixedResponseClient(payload)
    action = await preprocess_action_with_client(client, "draw my sword", "Kael")
    assert action.you == "You draw your sword"
    assert action.named == "Kael draws their sword"
    assert action.intent == "draw sword"


@pytest.mark.asyncio
async def test_preprocess_action_llm_failure_raises_llm_failed():
    client = FailingClient(Exception("LLM unavailable"))
    with pytest.raises(LlmFailed):
        await preprocess_action_with_client(client, "go north", "Alex")


@pytest.mark.asyncio
async def test_preprocess_action_parse_failure_raises_parse_failed():
    client = FixedResponseClient("this is not json")
    with pytest.raises(ParseFailed):
        await preprocess_action_with_client(client, "go north", "Alex")


@pytest.mark.asyncio
async def test_preprocess_action_output_too_long_raises():
    # Make output 3x longer than input (max is 2x).
    raw_input = "go"
    long_you = "You " + "x" * 100  # way longer than 2x "go"
    payload = make_preprocessed_json(
        you=long_you,
        named="Alex goes",
        intent="go",
    )
    client = FixedResponseClient(payload)
    with pytest.raises(OutputTooLong):
        await preprocess_action_with_client(client, raw_input, "Alex")


@pytest.mark.asyncio
async def test_preprocess_action_boolean_flags_parsed():
    payload = make_preprocessed_json(
        you="You use your healing potion",
        named="Rux uses their healing potion",
        intent="use healing potion",
        references_inventory=True,
    )
    client = FixedResponseClient(payload)
    action = await preprocess_action_with_client(client, "use healing potion", "Rux")
    assert action.references_inventory is True


@pytest.mark.asyncio
async def test_preprocess_action_npc_reference_flag():
    payload = make_preprocessed_json(
        you="You talk to the bartender",
        named="James talks to the bartender",
        intent="talk to bartender",
        references_npc=True,
    )
    client = FixedResponseClient(payload)
    action = await preprocess_action_with_client(client, "talk to bartender", "James")
    assert action.references_npc is True


@pytest.mark.asyncio
async def test_preprocess_action_location_reference_flag():
    payload = make_preprocessed_json(
        you="You head to the market",
        named="Kael heads to the market",
        intent="go to market",
        references_location=True,
    )
    client = FixedResponseClient(payload)
    action = await preprocess_action_with_client(client, "go to market", "Kael")
    assert action.references_location is True


# =========================================================================
# PreprocessedAction model
# =========================================================================


def test_preprocessed_action_default_booleans():
    action = PreprocessedAction(you="You look", named="Rux looks", intent="look")
    assert action.is_power_grab is False
    assert action.references_inventory is False
    assert action.references_npc is False
    assert action.references_ability is False
    assert action.references_location is False


def test_preprocessed_action_is_frozen():
    action = PreprocessedAction(you="You look", named="Rux looks", intent="look")
    with pytest.raises(Exception):
        action.you = "mutated"  # type: ignore[misc]


# =========================================================================
# Wiring test — imports from public sidequest.agents
# =========================================================================


@pytest.mark.asyncio
async def test_wiring_preprocess_action_with_client_importable():
    from sidequest.agents import preprocess_action_with_client

    payload = make_preprocessed_json(you="You look around", named="Rux looks around", intent="look")
    client = FixedResponseClient(payload)
    action = await preprocess_action_with_client(client, "look around", "Rux")
    assert action.intent == "look"
