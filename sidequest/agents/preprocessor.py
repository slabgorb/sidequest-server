"""Action Preprocessor — STT cleanup before player input reaches agents.

Port of sidequest-agents/src/preprocessor.rs.

Calls a haiku-tier LLM via an injected ClaudeLike client to clean
speech-to-text disfluencies (uh, um, like, you know, false starts,
repetitions) and rewrite player input into three perspectives:
second-person, named third-person, and neutral intent.

On LLM failure or timeout, raises PreprocessError — no silent fallbacks
per CLAUDE.md. The dispatch layer decides whether to retry, surface the
error, or skip the turn.
"""

from __future__ import annotations

import json
import logging

from sidequest.agents.claude_client import ClaudeClient, ClaudeLike, ClaudeResponse
from sidequest.game.turn import PreprocessedAction

logger = logging.getLogger(__name__)

# Haiku model identifier for fast preprocessing.
HAIKU_MODEL: str = "haiku"

# Timeout for preprocessing — long enough for Haiku to complete under load.
PREPROCESS_TIMEOUT: float = 30.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PreprocessError(Exception):
    """Base error from preprocessing — no silent fallbacks."""


class LlmFailed(PreprocessError):
    """The Haiku LLM call failed (timeout, subprocess error, etc.)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Haiku LLM call failed: {detail}")
        self.detail = detail


class ParseFailed(PreprocessError):
    """The Haiku response could not be parsed as a PreprocessedAction."""

    def __init__(self, response: str) -> None:
        super().__init__(f"Failed to parse Haiku response as PreprocessedAction: {response}")
        self.response = response


class OutputTooLong(PreprocessError):
    """The preprocessor produced output longer than 2x the raw input."""

    def __init__(
        self,
        raw_len: int,
        you_len: int,
        named_len: int,
        intent_len: int,
    ) -> None:
        super().__init__(
            f"Preprocessor output exceeded 2x input length "
            f"(raw={raw_len}, you={you_len}, named={named_len}, intent={intent_len})"
        )
        self.raw_len = raw_len
        self.you_len = you_len
        self.named_len = named_len
        self.intent_len = intent_len


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def preprocess_action(
    raw_input: str,
    char_name: str,
) -> PreprocessedAction:
    """Preprocess a raw player action into three perspectives via LLM.

    Fails loudly if Haiku is unavailable — no silent fallbacks.

    This is the default entry point — it constructs a real ClaudeClient and
    delegates to preprocess_action_with_client. Tests and sites that need to
    inject a mock should call preprocess_action_with_client directly.
    """
    client: ClaudeLike = ClaudeClient.with_timeout(PREPROCESS_TIMEOUT)
    return await preprocess_action_with_client(client, raw_input, char_name)


async def preprocess_action_with_client(
    client: ClaudeLike,
    raw_input: str,
    char_name: str,
) -> PreprocessedAction:
    """Preprocess a raw player action via an injected ClaudeLike client.

    Story 40-1: this is the one non-test consumer that proves the DI pattern
    works end-to-end. preprocess_action delegates here with a real ClaudeClient;
    tests inject a mock ClaudeClient with a fake spawn_fn.
    """
    prompt = build_prompt(raw_input, char_name)

    try:
        resp: ClaudeResponse = await client.send_with_model(prompt, HAIKU_MODEL)
    except Exception as e:
        raise LlmFailed(str(e)) from e

    response_text = resp.text
    logger.debug(
        "turn.preprocess.parse response_len=%d",
        len(response_text),
    )

    action = parse_response(response_text)
    if action is None:
        raise ParseFailed(response_text)

    max_len = len(raw_input) * 2
    if (
        len(action.you) > max_len
        or len(action.named) > max_len
        or len(action.intent) > max_len
    ):
        raise OutputTooLong(
            raw_len=len(raw_input),
            you_len=len(action.you),
            named_len=len(action.named),
            intent_len=len(action.intent),
        )

    logger.info(
        "Action preprocessed via LLM you=%r named=%r intent=%r",
        action.you,
        action.named,
        action.intent,
    )
    return action


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def build_prompt(raw_input: str, char_name: str) -> str:
    """Build the LLM prompt for action preprocessing."""
    return (
        f"You are a speech-to-text cleanup preprocessor for a tabletop RPG game.\n\n"
        f"Clean the following player input of STT disfluencies "
        f"(uh, um, like, you know, false starts, repetitions).\n\n"
        f"Rules:\n"
        f"- Preserve all quoted dialogue VERBATIM.\n"
        f"- Do NOT add adjectives, adverbs, or emotions that weren't in the original.\n"
        f"- Each output field must be no longer than 2x the input length.\n"
        f"- Output ONLY valid JSON, no markdown fences, no explanation.\n\n"
        f"Character name: {char_name}\n\n"
        f'Player input: "{raw_input}"\n\n'
        f"Respond with JSON having exactly eight keys:\n"
        f'- "you": second-person rewrite (e.g., "You draw your sword")\n'
        f'- "named": third-person with character name (e.g., "{char_name} draws their sword")\n'
        f'- "intent": neutral, no pronouns (e.g., "draw sword")\n'
        f'- "is_power_grab": true ONLY if the player is genuinely attempting to seize extraordinary power\n'
        f"  (unlimited resources, godlike abilities, time control, invincibility, summoning weapons from\n"
        f'  nothing, killing everyone). The test: would a tabletop DM say "you can\'t just do that"?\n'
        f'  Casual mention does NOT count: "I wish I hadn\'t eaten that" = false.\n'
        f'  "I wish for unlimited gold from the genie" = true.\n'
        f'- "references_inventory": true if the player mentions using, checking, equipping, trading,\n'
        f'  dropping, or interacting with items, equipment, or possessions. "I look around" = false.\n'
        f'  "I use my healing potion" = true. "I check what I\'m carrying" = true.\n'
        f'- "references_npc": true if the player addresses or mentions a specific character by name\n'
        f'  or role. "I explore the cave" = false. "I talk to the bartender" = true.\n'
        f'- "references_ability": true if the player invokes or activates a power, mutation, skill,\n'
        f'  spell, or supernatural ability. "I walk north" = false. "I use my psychic echo" = true.\n'
        f'- "references_location": true if the player mentions a specific place by name or attempts\n'
        f'  to travel somewhere. "I look around" = false. "I head to the market" = true.'
    )


def parse_response(response: str) -> PreprocessedAction | None:
    """Parse the LLM response as a PreprocessedAction JSON object."""
    # Try direct parse first
    try:
        return PreprocessedAction.model_validate_json(response)
    except Exception:
        pass

    # Try extracting JSON from markdown fences or surrounding text
    start = response.find("{")
    end = response.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = response[start : end + 1]
        try:
            return PreprocessedAction.model_validate_json(json_str)
        except Exception:
            pass

    return None
