"""Tests for sidequest/agents/orchestrator.py — Phase 1 narration pipeline.

No live Claude CLI calls. All Claude interactions are mocked via ClaudeClient
with a canned spawn_fn.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sidequest.agents.claude_client import ClaudeClient
from sidequest.agents.orchestrator import (
    ActionRewrite,
    BeatSelection,
    NarrationTurnResult,
    NarratorPromptTier,
    NpcMention,
    Orchestrator,
    TurnContext,
    _extract_game_patch_json,
    _strip_json_fence,
    extract_structured_from_response,
)
from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.agents.prompt_framework.types import AttentionZone
from sidequest.protocol.dispatch import (
    DispatchPackage,
    NarratorDirective,
    PlayerDispatch,
    VisibilityTag,
)

# ---------------------------------------------------------------------------
# Helpers — fake subprocess process and canned spawn
# ---------------------------------------------------------------------------


class FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in for tests."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = b""
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def make_json_response(text: str, session_id: str = "test-session-001") -> bytes:
    """Build a minimal Claude CLI JSON envelope from a canned text response."""
    payload = {
        "result": text,
        "session_id": session_id,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    return json.dumps(payload).encode()


def make_spawn_fn(text: str, session_id: str = "test-session-001"):
    """Return a spawn_fn that returns the given canned narrator text."""

    async def spawn_fn(command: str, *args: str, env: Any = None, **kwargs: Any) -> FakeProcess:
        return FakeProcess(stdout=make_json_response(text, session_id=session_id))

    return spawn_fn


def make_canned_client(narration_text: str, session_id: str = "test-session-001") -> ClaudeClient:
    """Build a ClaudeClient whose subprocess always returns narration_text."""
    return ClaudeClient(spawn_fn=make_spawn_fn(narration_text, session_id=session_id))


# ---------------------------------------------------------------------------
# _extract_game_patch_json
# ---------------------------------------------------------------------------


def test_extract_game_patch_json_from_game_patch_fence():
    raw = '**The Tavern**\n\nSome prose.\n\n```game_patch\n{"location": "Tavern"}\n```'
    result = _extract_game_patch_json(raw)
    assert result == {"location": "Tavern"}


def test_extract_game_patch_json_from_json_fence_fallback():
    raw = '**The Tavern**\n\nSome prose.\n\n```json\n{"location": "Docks"}\n```'
    result = _extract_game_patch_json(raw)
    assert result == {"location": "Docks"}


def test_extract_game_patch_json_returns_empty_dict_on_no_fence():
    raw = "**The Tavern**\n\nSome prose with no JSON block."
    result = _extract_game_patch_json(raw)
    assert result == {}


def test_extract_game_patch_json_warns_on_malformed_json(caplog):
    import logging
    raw = '```game_patch\n{invalid json}\n```'
    with caplog.at_level(logging.WARNING, logger="sidequest.agents.orchestrator"):
        result = _extract_game_patch_json(raw)
    assert result == {}
    assert "failed to parse" in caplog.text


# ---------------------------------------------------------------------------
# _strip_json_fence
# ---------------------------------------------------------------------------


def test_strip_json_fence_removes_game_patch_block():
    raw = "**The Tavern**\n\nSome prose.\n\n```game_patch\n{}\n```"
    assert _strip_json_fence(raw) == "**The Tavern**\n\nSome prose."


def test_strip_json_fence_removes_json_block():
    raw = "Prose here.\n\n```json\n{}\n```"
    assert _strip_json_fence(raw) == "Prose here."


def test_strip_json_fence_returns_text_unchanged_if_no_fence():
    raw = "**The Tavern**\n\nSome prose only."
    assert _strip_json_fence(raw) == raw.strip()


def test_strip_json_fence_warns_and_discards_post_patch_content(caplog):
    import logging
    raw = "Prose.\n\n```game_patch\n{}\n```\n\nNote: I've been helpful."
    with caplog.at_level(logging.WARNING, logger="sidequest.agents.orchestrator"):
        result = _strip_json_fence(raw)
    assert result == "Prose."
    assert "discarding post-patch content" in caplog.text


# ---------------------------------------------------------------------------
# extract_structured_from_response
# ---------------------------------------------------------------------------


def test_extract_structured_returns_prose():
    raw = "**The Docks**\n\nThe smell of brine.\n\n```game_patch\n{}\n```"
    result = extract_structured_from_response(raw)
    assert result["prose"] == "**The Docks**\n\nThe smell of brine."


def test_extract_structured_extracts_location():
    raw = '```game_patch\n{"location": "Docks"}\n```'
    result = extract_structured_from_response(raw)
    assert result["location"] == "Docks"


def test_extract_structured_extracts_footnotes():
    raw = '```game_patch\n{"footnotes": [{"summary": "The key is lost", "category": "Lore", "is_new": true}]}\n```'
    result = extract_structured_from_response(raw)
    assert len(result["footnotes"]) == 1
    assert result["footnotes"][0]["summary"] == "The key is lost"


def test_extract_structured_extracts_items_gained():
    raw = '```game_patch\n{"items_gained": [{"name": "Rusty Key", "description": "An old key", "category": "misc"}]}\n```'
    result = extract_structured_from_response(raw)
    assert len(result["items_gained"]) == 1
    assert result["items_gained"][0]["name"] == "Rusty Key"


def test_extract_structured_extracts_beat_selections():
    raw = '```game_patch\n{"beat_selections": [{"actor": "Player", "beat_id": "attack", "target": "Goblin"}]}\n```'
    result = extract_structured_from_response(raw)
    assert len(result["beat_selections"]) == 1
    assert result["beat_selections"][0]["actor"] == "Player"


def test_extract_structured_extracts_confrontation():
    raw = '```game_patch\n{"confrontation": "combat"}\n```'
    result = extract_structured_from_response(raw)
    assert result["confrontation"] == "combat"


def test_extract_structured_extracts_npcs_met_alias():
    """npcs_met and npcs_present are both valid labels."""
    raw = '```game_patch\n{"npcs_met": ["Toggler"]}\n```'
    result = extract_structured_from_response(raw)
    assert len(result["npcs_present"]) == 1


def test_extract_structured_extracts_gold_change():
    raw = '```game_patch\n{"gold_change": -10}\n```'
    result = extract_structured_from_response(raw)
    assert result["gold_change"] == -10


def test_extract_structured_extracts_action_rewrite():
    raw = '```game_patch\n{"action_rewrite": {"you": "You look around", "named": "Kael looks around", "intent": "look around"}}\n```'
    result = extract_structured_from_response(raw)
    assert result["action_rewrite"]["you"] == "You look around"


def test_extract_structured_extracts_affinity_progress():
    raw = '```game_patch\n{"affinity_progress": [{"name": "combat_mastery", "delta": 1}]}\n```'
    result = extract_structured_from_response(raw)
    assert result["affinity_progress"] == [("combat_mastery", 1)]


def test_extract_structured_empty_patch_returns_defaults():
    raw = "Some prose.\n\n```game_patch\n{}\n```"
    result = extract_structured_from_response(raw)
    assert result["footnotes"] == []
    assert result["items_gained"] == []
    assert result["beat_selections"] == []
    assert result["confrontation"] is None
    assert result["location"] is None


# ---------------------------------------------------------------------------
# NpcMention.from_value
# ---------------------------------------------------------------------------


def test_npc_mention_from_full_struct():
    npc = NpcMention.from_value({"name": "Toggler Copperjaw", "role": "blacksmith", "is_new": True})
    assert npc.name == "Toggler Copperjaw"
    assert npc.role == "blacksmith"
    assert npc.is_new is True


def test_npc_mention_from_bare_string():
    npc = NpcMention.from_value("Nub")
    assert npc.name == "Nub"
    assert npc.role == ""
    assert npc.is_new is False


def test_npc_mention_vec_mixed_formats():
    values = [{"name": "Toggler", "role": "smith"}, "Nub", {"name": "Vera"}]
    npcs = [NpcMention.from_value(v) for v in values]
    assert len(npcs) == 3
    assert npcs[0].name == "Toggler"
    assert npcs[1].name == "Nub"
    assert npcs[2].name == "Vera"


# ---------------------------------------------------------------------------
# BeatSelection.from_dict
# ---------------------------------------------------------------------------


def test_beat_selection_from_dict():
    bs = BeatSelection.from_dict({"actor": "Player", "beat_id": "attack", "target": "Goblin"})
    assert bs.actor == "Player"
    assert bs.beat_id == "attack"
    assert bs.target == "Goblin"


def test_beat_selection_no_target():
    bs = BeatSelection.from_dict({"actor": "Goblin", "beat_id": "defend"})
    assert bs.target is None


# ---------------------------------------------------------------------------
# ActionRewrite
# ---------------------------------------------------------------------------


def test_action_rewrite_from_dict():
    ar = ActionRewrite.from_dict({"you": "You draw your sword", "named": "Kael draws their sword", "intent": "draw sword"})
    assert ar.you == "You draw your sword"
    assert ar.intent == "draw sword"


# ---------------------------------------------------------------------------
# Orchestrator — session lifecycle
# ---------------------------------------------------------------------------


def test_orchestrator_starts_with_no_session():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    assert not orch.has_active_narrator_session()


def test_orchestrator_select_prompt_tier_full_when_no_session():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext()
    assert orch.select_prompt_tier(context) == NarratorPromptTier.Full


def test_orchestrator_select_prompt_tier_delta_after_session_set():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    orch.set_narrator_session_id("existing-session")
    context = TurnContext(genre="caverns_and_claudes")
    # Also set session genre to avoid genre-switch detection
    orch._session_genre = "caverns_and_claudes"
    assert orch.select_prompt_tier(context) == NarratorPromptTier.Delta


def test_orchestrator_genre_switch_forces_full_tier():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    orch.set_narrator_session_id("existing-session")
    orch._session_genre = "mutant_wasteland"
    context = TurnContext(genre="caverns_and_claudes")
    tier = orch.select_prompt_tier(context)
    assert tier == NarratorPromptTier.Full
    assert not orch.has_active_narrator_session()


def test_orchestrator_reset_clears_session():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    orch.set_narrator_session_id("some-session")
    orch.reset_narrator_session()
    assert not orch.has_active_narrator_session()


# ---------------------------------------------------------------------------
# Orchestrator.build_narrator_prompt — structure
# ---------------------------------------------------------------------------


async def test_build_narrator_prompt_full_contains_narrator_identity():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", state_summary="You are in a tavern.")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "Game Master" in prompt


async def test_build_narrator_prompt_full_contains_output_format():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "game_patch" in prompt


async def test_build_narrator_prompt_contains_player_action():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    prompt, _ = await orch.build_narrator_prompt("examine the door", context, tier=NarratorPromptTier.Full)
    assert "examine the door" in prompt
    assert "Kael" in prompt


async def test_build_narrator_prompt_includes_genre_identity():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", genre="caverns_and_claudes")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "caverns and claudes" in prompt


async def test_build_narrator_prompt_full_contains_verbosity_limit():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", narrator_verbosity="concise")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "400 characters" in prompt


async def test_build_narrator_prompt_full_contains_vocabulary_section():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", narrator_vocabulary="epic")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "archaic" in prompt


async def test_build_narrator_prompt_includes_state_summary():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(
        character_name="Kael",
        state_summary="You are in a dark cave. HP: 10/10.",
    )
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "dark cave" in prompt


async def test_build_narrator_prompt_includes_lore_context_when_provided():
    # Story 37-33: retrieved lore from semantic search should land in
    # the Valley zone so the narrator has canonical world detail to
    # weave in without asking the player.
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(
        character_name="Kael",
        lore_context=(
            "<lore>\n"
            "# Relevant lore retrieved for this turn\n"
            "- [history · id=castle · similarity=0.92] An ancient castle stands on the hill.\n"
            "</lore>"
        ),
    )
    prompt, _ = await orch.build_narrator_prompt("approach the castle", context, tier=NarratorPromptTier.Full)
    assert "<lore>" in prompt
    assert "ancient castle" in prompt


async def test_build_narrator_prompt_omits_lore_section_when_none():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", lore_context=None)
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "<lore>" not in prompt


async def test_build_narrator_prompt_encounter_rules_when_in_combat():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", in_combat=True)
    prompt, _ = await orch.build_narrator_prompt("attack goblin", context, tier=NarratorPromptTier.Full)
    assert "COMBAT NARRATION RULES" in prompt


async def test_build_narrator_prompt_no_encounter_rules_when_not_in_combat():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", in_combat=False, in_chase=False)
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "COMBAT NARRATION RULES" not in prompt


async def test_build_narrator_prompt_delta_excludes_static_sections():
    """Delta tier should omit narrator identity (already in session)."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    full_prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    delta_prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Delta)
    # Full prompt contains identity content; delta does not
    assert "narrator_identity" not in delta_prompt or len(delta_prompt) < len(full_prompt)


async def test_build_narrator_prompt_delta_still_contains_output_format():
    """Output format must be on every tier — narrator needs game_patch schema always."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Delta)
    assert "game_patch" in prompt


async def test_build_narrator_prompt_delta_still_contains_genre_identity():
    """Genre identity must be on every tier (playtest fix 2026-04-05)."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", genre="road_warrior")
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Delta)
    assert "road warrior" in prompt


async def test_build_narrator_prompt_player_action_last_in_zone_order():
    """player_action (Recency) must appear after identity (Primacy) in composed output."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    prompt, _ = await orch.build_narrator_prompt("cast spell", context, tier=NarratorPromptTier.Full)
    identity_pos = prompt.find("Game Master")
    action_pos = prompt.find("cast spell")
    assert identity_pos < action_pos


async def test_build_narrator_prompt_trope_context_injected():
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    context = TurnContext(
        character_name="Kael",
        pending_trope_context="WEAVE THIS: The ancient curse stirs.",
    )
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "WEAVE THIS" in prompt


# ---------------------------------------------------------------------------
# Orchestrator.run_narration_turn — async turn pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_narration_turn_returns_narration():
    narration_text = (
        "**The Tavern**\n\nThe smell of stale ale fills the air.\n\n"
        "```game_patch\n{}\n```"
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", current_location="The Tavern")
    result = await orch.run_narration_turn("look around", context)
    assert "stale ale" in result.narration
    assert not result.is_degraded


@pytest.mark.asyncio
async def test_run_narration_turn_stores_session_id():
    narration_text = "**The Tavern**\n\nProse.\n\n```game_patch\n{}\n```"
    client = make_canned_client(narration_text, session_id="session-xyz")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    await orch.run_narration_turn("look around", context)
    assert orch.has_active_narrator_session()
    with orch._session_lock:
        assert orch._narrator_session_id == "session-xyz"


@pytest.mark.asyncio
async def test_run_narration_turn_second_call_uses_delta_tier():
    narration_text = "**The Tavern**\n\nProse.\n\n```game_patch\n{}\n```"
    client = make_canned_client(narration_text, session_id="sess-001")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", genre="caverns_and_claudes")
    # First turn
    await orch.run_narration_turn("look around", context)
    # Second turn — should use Delta tier
    result = await orch.run_narration_turn("move north", context)
    assert result.prompt_tier == NarratorPromptTier.Delta


@pytest.mark.asyncio
async def test_run_narration_turn_extracts_location():
    narration_text = (
        "**The Docks**\n\nThe sea glitters.\n\n"
        '```game_patch\n{"location": "The Docks"}\n```'
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("look around", context)
    assert result.location == "The Docks"


@pytest.mark.asyncio
async def test_run_narration_turn_extracts_confrontation():
    narration_text = (
        "**The Alley**\n\nThe bandit draws a knife.\n\n"
        '```game_patch\n{"confrontation": "combat"}\n```'
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("attack the bandit", context)
    assert result.confrontation == "combat"


@pytest.mark.asyncio
async def test_run_narration_turn_extracts_npcs():
    narration_text = (
        "**The Market**\n\nThe vendor smiles.\n\n"
        '```game_patch\n{"npcs_met": ["Nub the Vendor"]}\n```'
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("talk to vendor", context)
    assert len(result.npcs_present) == 1
    assert result.npcs_present[0].name == "Nub the Vendor"


@pytest.mark.asyncio
async def test_run_narration_turn_degraded_on_claude_error():
    """ADR-005: CLI failure returns degraded response, not exception."""

    async def failing_spawn(command: str, *args: str, **kwargs: Any) -> Any:
        raise RuntimeError("Claude binary not found")

    client = ClaudeClient(spawn_fn=failing_spawn)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael", current_location="The Tavern")
    result = await orch.run_narration_turn("look around", context)
    assert result.is_degraded
    assert "The Tavern" in result.narration


@pytest.mark.asyncio
async def test_run_narration_turn_records_otel_fields():
    narration_text = "**The Tavern**\n\nProse.\n\n```game_patch\n{}\n```"
    client = make_canned_client(narration_text, session_id="sess-123")
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("look around", context)
    assert result.agent_name == "narrator"
    assert result.agent_duration_ms is not None
    assert result.token_count_in is not None
    assert result.token_count_out is not None


@pytest.mark.asyncio
async def test_run_narration_turn_warns_missing_action_rewrite(caplog):
    import logging
    narration_text = "**The Tavern**\n\nProse.\n\n```game_patch\n{}\n```"
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    with caplog.at_level(logging.WARNING, logger="sidequest.agents.orchestrator"):
        await orch.run_narration_turn("look around", context)
    assert "action_rewrite absent" in caplog.text


@pytest.mark.asyncio
async def test_run_narration_turn_extracts_items_gained():
    narration_text = (
        "**The Chest**\n\nYou find a rusty key.\n\n"
        '```game_patch\n{"items_gained": [{"name": "Rusty Key", "description": "An old key", "category": "misc"}]}\n```'
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("open chest", context)
    assert len(result.items_gained) == 1
    assert result.items_gained[0]["name"] == "Rusty Key"


@pytest.mark.asyncio
async def test_run_narration_turn_extracts_status_changes():
    """Wiring: status_changes from game_patch flows through to NarrationTurnResult."""
    narration_text = (
        "**The Arena**\n\nSam ducks the swing.\n\n"
        "```game_patch\n"
        '{"status_changes": [{"actor": "Sam", "status": {"text": "Bruised Ribs", "severity": "Wound"}}]}\n'
        "```"
    )
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(character_name="Sam")
    result = await orch.run_narration_turn("defend", context)
    assert result.status_changes == [
        {"actor": "Sam", "status": {"text": "Bruised Ribs", "severity": "Wound"}},
    ]


@pytest.mark.asyncio
async def test_run_narration_turn_genre_prompts_injected():
    """Genre prompts from prompts.yaml appear in the assembled prompt."""
    from sidequest.genre.models.narrative import Prompts
    narration_text = "**The Cavern**\n\nProse.\n\n```game_patch\n{}\n```"
    client = make_canned_client(narration_text)
    orch = Orchestrator(client=client)
    context = TurnContext(
        character_name="Kael",
        genre="caverns_and_claudes",
        genre_prompts=Prompts(
            narrator="Narrate with dungeon grit.",
            combat="Keep combat brutal.",
            npc="NPCs speak in riddles.",
            world_state="Track the dungeon state.",
        ),
    )
    prompt, _ = await orch.build_narrator_prompt("look around", context, tier=NarratorPromptTier.Full)
    assert "dungeon grit" in prompt
    assert "NPCs speak in riddles" in prompt


# ---------------------------------------------------------------------------
# Group A Task 2 — ActionFlags removal tests
# ---------------------------------------------------------------------------


def test_narration_turn_result_has_no_action_flags():
    """Group A Task 2 — ActionFlags dataclass is retired."""
    from dataclasses import fields
    field_names = {f.name for f in fields(NarrationTurnResult)}
    assert "action_flags" not in field_names, (
        "action_flags still on NarrationTurnResult"
    )


def test_action_flags_class_is_removed_from_orchestrator():
    """Group A Task 2 — ActionFlags dataclass itself is gone."""
    from sidequest.agents import orchestrator
    assert not hasattr(orchestrator, "ActionFlags"), (
        "ActionFlags dataclass still defined in orchestrator module"
    )


def test_action_flags_not_exported_from_agents_package():
    """Group A Task 2 — ActionFlags removed from agents package exports."""
    from sidequest.agents import __all__
    assert "ActionFlags" not in __all__, (
        "ActionFlags still exported from sidequest.agents.__all__"
    )


def test_action_rewrite_still_present():
    """Guard: ActionRewrite is LIVE — must not be touched."""
    from dataclasses import fields

    from sidequest.agents.orchestrator import ActionRewrite
    assert ActionRewrite is not None
    field_names = {f.name for f in fields(NarrationTurnResult)}
    assert "action_rewrite" in field_names, (
        "action_rewrite must remain on NarrationTurnResult — not in scope for removal"
    )


def test_narration_turn_result_has_no_classified_intent():
    """Group A Task 3 — classified_intent dead hardcode retired."""
    from dataclasses import fields
    field_names = {f.name for f in fields(NarrationTurnResult)}
    assert "classified_intent" not in field_names, (
        "classified_intent still on NarrationTurnResult"
    )


def test_orchestrator_module_has_no_classified_intent_hardcode():
    """Group A Task 3 — no classified_intent = 'exploration' assignment in source."""
    import inspect

    from sidequest.agents import orchestrator
    source = inspect.getsource(orchestrator)
    assert 'classified_intent = "exploration"' not in source, (
        'Hardcoded classified_intent = "exploration" still present'
    )
    assert "classified_intent = 'exploration'" not in source, (
        "Hardcoded classified_intent = 'exploration' still present"
    )


def test_turn_context_defaults_dispatch_package_to_none():
    """Group B Task 8 — optional field defaults to None.

    All other TurnContext fields have defaults; constructing with no args
    should succeed and dispatch_package should read as None.
    """
    tc = TurnContext()
    assert tc.dispatch_package is None


def test_turn_context_accepts_dispatch_package():
    """Group B Task 8 — the new field is populated via kwarg."""
    pkg = DispatchPackage(
        turn_id="t1", per_player=[], cross_player=[],
        confidence_global=1.0, degraded=False, degraded_reason=None,
    )
    tc = TurnContext(dispatch_package=pkg)
    assert tc.dispatch_package is pkg


# ---------------------------------------------------------------------------
# Task 9 — narrator_directives section injection from DispatchPackage
# ---------------------------------------------------------------------------


def _tag_all() -> VisibilityTag:
    return VisibilityTag(
        visible_to="all",
        perception_fidelity={},
        secrets_for=[],
        redact_from_narrator_canonical=False,
    )


async def test_build_narrator_prompt_registers_narrator_directives_when_present():
    """When TurnContext.dispatch_package has authored directives, the narrator
    prompt registry contains a 'narrator_directives' section and the directive
    payloads appear in the rendered prompt text."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    pkg = DispatchPackage(
        turn_id="t1",
        per_player=[
            PlayerDispatch(
                player_id="player:Alice",
                raw_action="Let's go!",
                resolved=[],
                dispatch=[],
                lethality=[],
                narrator_instructions=[
                    NarratorDirective(
                        kind="must_not_narrate",
                        payload="zzz-must-not-payload-zzz",
                        visibility=_tag_all(),
                    ),
                    NarratorDirective(
                        kind="must_narrate",
                        payload="zzz-must-narrate-payload-zzz",
                        visibility=_tag_all(),
                    ),
                ],
            )
        ],
        cross_player=[],
        confidence_global=1.0,
        degraded=False,
        degraded_reason=None,
    )
    ctx = TurnContext(dispatch_package=pkg)

    prompt_text, registry = await orch.build_narrator_prompt(
        "Let's go!", ctx, tier=NarratorPromptTier.Full
    )

    assert "zzz-must-not-payload-zzz" in prompt_text
    assert "zzz-must-narrate-payload-zzz" in prompt_text

    # Strong check: section is registered under the expected name.
    section_names = [s.name for s in registry.registry(orch._narrator.name())]
    assert "narrator_directives" in section_names


async def test_build_narrator_prompt_omits_narrator_directives_when_no_dispatch_package():
    """When dispatch_package is None, the prompt does NOT contain the
    narrator_directives section (no decomposer payload strings)."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    ctx = TurnContext(dispatch_package=None)

    prompt_text, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Full
    )

    assert "zzz-must-not-payload-zzz" not in prompt_text
    assert "zzz-must-narrate-payload-zzz" not in prompt_text

    section_names = [s.name for s in registry.registry(orch._narrator.name())]
    assert "narrator_directives" not in section_names


# ---------------------------------------------------------------------------
# Group G Task 5 — structural hiding in narrator prompt assembly
# ---------------------------------------------------------------------------


def _tag_redacted(who: str) -> VisibilityTag:
    return VisibilityTag(
        visible_to=[who],
        perception_fidelity={},
        secrets_for=[who],
        redact_from_narrator_canonical=True,
    )


async def test_build_narrator_prompt_strips_redacted_directive_payload():
    """A NarratorDirective flagged ``redact_from_narrator_canonical`` MUST NOT
    have its payload appear in the rendered narrator prompt — the LLM
    cannot leak what it never saw.

    Paired with the orchestrator exposing the removed entries via
    ``_last_secret_routes`` for SECRET_NOTE routing (Task 6)."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    pkg = DispatchPackage(
        turn_id="t1",
        per_player=[
            PlayerDispatch(
                player_id="player:Alice",
                raw_action="poison wine",
                narrator_instructions=[
                    NarratorDirective(
                        kind="canonical_only_do_not_reveal_to_others",
                        payload="zzz-SECRET-alice-poisons-wine-zzz",
                        visibility=_tag_redacted("player:Alice"),
                    ),
                    NarratorDirective(
                        kind="must_narrate",
                        payload="zzz-PUBLIC-dogs-bark-zzz",
                        visibility=_tag_all(),
                    ),
                ],
            )
        ],
        confidence_global=1.0,
    )
    ctx = TurnContext(dispatch_package=pkg)

    prompt_text, registry = await orch.build_narrator_prompt(
        "poison wine", ctx, tier=NarratorPromptTier.Full
    )

    # The redacted payload MUST NOT appear in the prompt string.
    assert "zzz-SECRET-alice-poisons-wine-zzz" not in prompt_text
    # The open directive MUST still be present.
    assert "zzz-PUBLIC-dogs-bark-zzz" in prompt_text

    # The removed entry is exposed on the orchestrator for Task 6.
    assert len(orch._last_secret_routes) == 1
    removed = orch._last_secret_routes[0]
    assert isinstance(removed, NarratorDirective)
    assert removed.payload == "zzz-SECRET-alice-poisons-wine-zzz"


async def test_build_narrator_prompt_clears_secret_routes_when_no_dispatch_package():
    """Calls with no DispatchPackage must leave ``_last_secret_routes`` empty,
    so a previous turn's secrets never leak into a future turn's result."""
    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    # Pretend a previous turn left stale state behind.
    orch._last_secret_routes = [object()]

    ctx = TurnContext(dispatch_package=None)
    await orch.build_narrator_prompt("look", ctx, tier=NarratorPromptTier.Full)

    assert orch._last_secret_routes == []


# ---------------------------------------------------------------------------
# Group G Task 7 — canonical-leak audit wiring in run_narration_turn
# ---------------------------------------------------------------------------


async def test_run_narration_turn_emits_leak_audit_span_with_zero_leaks(
    otel_capture,
):
    """run_narration_turn must emit the ``narrator.canonical_leak_audit`` span
    for every turn that had a DispatchPackage, with ``leaks_detected=0`` when
    the canonical prose does not contain any redacted-entity tokens.

    This is the safety-net verification: structural hiding (Task 5) removed
    the redacted entry from the prompt, and the canned narration below does
    not mention the hidden target, so the audit fires clean."""
    from sidequest.game.session import NpcRegistryEntry
    from sidequest.protocol.dispatch import SubsystemDispatch

    # Canned narrator response — no reference to the hidden target.
    client = make_canned_client("The evening wears on at the inn.")
    orch = Orchestrator(client=client)

    pkg = DispatchPackage(
        turn_id="t-audit",
        per_player=[
            PlayerDispatch(
                player_id="player:Alice",
                raw_action="sneak and strike",
                dispatch=[
                    SubsystemDispatch(
                        subsystem="lethal_strike",
                        params={"target": "Rickard"},
                        idempotency_key="k1",
                        visibility=_tag_redacted("player:Alice"),
                    ),
                ],
            )
        ],
        confidence_global=1.0,
    )
    ctx = TurnContext(
        dispatch_package=pkg,
        npc_registry=[NpcRegistryEntry(name="Rickard", role="guard")],
    )

    await orch.run_narration_turn("sneak and strike", ctx)

    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "narrator.canonical_leak_audit"
    ]
    assert len(spans) == 1, (
        f"expected exactly one leak_audit span, got {[s.name for s in otel_capture.get_finished_spans()]}"
    )
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("leaks_detected") == 0
    assert attrs.get("turn_id") == "t-audit"
    assert attrs.get("redact_tag_count") == 1


async def test_run_narration_turn_skips_leak_audit_when_no_dispatch_package(
    otel_capture,
):
    """With no DispatchPackage, there is nothing to audit — the span must not
    fire. Keeps the expected-zero telemetry shape meaningful: a span in the
    stream means we ran an audit, not that we shrugged."""
    client = make_canned_client("Nothing happens.")
    orch = Orchestrator(client=client)
    ctx = TurnContext(dispatch_package=None)

    await orch.run_narration_turn("look", ctx)

    spans = [
        s for s in otel_capture.get_finished_spans()
        if s.name == "narrator.canonical_leak_audit"
    ]
    assert spans == []


# ---------------------------------------------------------------------------
# Task 18 — module-level run_narration_turn wrapper: signal-clear safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_narration_turn_clears_pending_resolution_signal_on_error(
    monkeypatch,
):
    """If the orchestrator raises, the one-shot resolution signal still gets
    cleared from the snapshot — otherwise a transient failure causes the
    [ENCOUNTER RESOLVED] zone to fire twice on the next turn (and the
    encounter_resolution_signal_consumed_span fires twice).

    Wraps the bare ``orchestrator.run_narration_turn(...) → assignment →
    cleanup`` shape in a try/finally so the cleanup is exception-safe.
    """
    from types import SimpleNamespace

    from sidequest.agents import orchestrator as orch_mod
    from sidequest.game.resolution_signal import ResolutionSignal
    from sidequest.game.session import GameSnapshot

    # Snapshot with a pending resolution signal — the thing the wrapper must
    # clear even on failure.
    snapshot = GameSnapshot(
        genre_slug="test",
        world_slug="test",
        location="The Pit",
        pending_resolution_signal=ResolutionSignal(
            encounter_type="combat",
            outcome="opponent_victory",
            final_player_metric=4,
            final_opponent_metric=11,
        ),
    )
    assert snapshot.pending_resolution_signal is not None  # arrange sanity

    # Minimal genre stand-in: the wrapper only reads ``audio`` (for sfx) and
    # ``prompts`` (passed through to TurnContext, never executed because we
    # short-circuit the orchestrator).
    fake_genre = SimpleNamespace(audio=SimpleNamespace(), prompts=None)

    async def boom(self, player_action, context):
        raise RuntimeError("simulated orchestrator failure")

    monkeypatch.setattr(Orchestrator, "run_narration_turn", boom)

    client = make_canned_client("unused")

    with pytest.raises(RuntimeError, match="simulated orchestrator failure"):
        await orch_mod.run_narration_turn(
            client=client,
            session=snapshot,
            genre=fake_genre,
            player_action="attack",
        )

    # The contract: signal cleared even though the orchestrator raised.
    assert snapshot.pending_resolution_signal is None


# ---------------------------------------------------------------------------
# None-dispatch-package path — pins Group B / Group G guard behavior
# ---------------------------------------------------------------------------


async def test_build_narrator_prompt_with_none_dispatch_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TurnContext.dispatch_package is None, build_narrator_prompt
    must skip redact_dispatch_package, skip dispatch-bank execution,
    and produce a prompt without subsystem-injected sections."""
    import sidequest.agents.prompt_redaction as _redaction_mod
    import sidequest.agents.subsystems as _subsystems_mod

    client = make_canned_client("narration")
    orch = Orchestrator(client=client)
    # dispatch_package defaults to None — explicit for documentation clarity.
    context = TurnContext(character_name="Kael", dispatch_package=None)

    redact_called = False
    bank_called = False

    def _fake_redact(*args, **kwargs):  # pragma: no cover — must NOT be called
        nonlocal redact_called
        redact_called = True
        raise AssertionError("redact_dispatch_package called on None path")

    async def _fake_bank(*args, **kwargs):  # pragma: no cover — must NOT be called
        nonlocal bank_called
        bank_called = True
        raise AssertionError("run_dispatch_bank called on None path")

    monkeypatch.setattr(_redaction_mod, "redact_dispatch_package", _fake_redact)
    monkeypatch.setattr(_subsystems_mod, "run_dispatch_bank", _fake_bank)

    prompt_text, _registry = await orch.build_narrator_prompt(
        action="I look around.",
        context=context,
        tier=NarratorPromptTier.Full,
    )

    assert redact_called is False
    assert bank_called is False
    assert prompt_text  # prompt was built successfully
