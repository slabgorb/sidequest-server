"""SubjectExtractor — LLM-based subject extraction for the render pipeline.

Uses Claude CLI to extract structured visual subjects from narrative text,
producing richer StageCue data than regex rules alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sidequest.renderer.models import RenderTier

logger = logging.getLogger(__name__)

VALID_TAGS = frozenset(
    {"combat", "magic", "special_effect", "character", "location", "atmosphere"}
)
VALID_MOODS = frozenset(
    {"ominous", "tense", "mystical", "dramatic", "melancholic", "atmospheric"}
)
_VALID_TIER_NAMES = {t.name for t in RenderTier}
_VALID_TIER_VALUES = {t.value for t in RenderTier}
_REQUIRED_FIELDS = {"subject", "tags", "tier", "mood"}

_PROMPT_TEMPLATE = (
    "Extract the visual scene from this narrative for an image generation model. "
    "Return ONLY valid JSON with these fields:\n"
    "- subject (str, ≤100 chars): ONLY describe what a painter would SEE. "
    "Physical objects, people, lighting, weather, setting. "
    "Use simple concrete words (sword, old man, dark forest, stone tower, firelight). "
    "IGNORE all quoted dialogue and speech. Extract only the physical scene, not what characters say. "
    "NO emotions, motivations, plot, metaphors, or abstract concepts. "
    "NO literary words like treacherous, foreboding, ephemeral — use dangerous, dark, fading instead.\n"
    "- tags (list of str from: combat, magic, special_effect, character, location, atmosphere)\n"
    "- tier (one of: {tiers}). "
    "Use TEXT_OVERLAY for documents, notices, letters, scrolls, inscriptions, or posted messages.\n"
    "- mood (one of: ominous, tense, mystical, dramatic, melancholic, atmospheric)\n"
    "\nIf the narrative has no visual content, return {{\"subject\": null}}.\n"
    "\nNarrative: {narrative}"
)

_RESPONSE_TIMEOUT = 30


class SubjectExtractor:
    """Extract structured visual subjects from narrative text via Claude CLI."""

    def __init__(self, client: Any = None) -> None:
        # client param kept for test injection compatibility
        self._client = client

    async def extract(self, narrative: str) -> dict | None:
        """Extract visual subject from narrative text.

        Returns a dict with subject, tags, tier, mood — or None on failure.
        Never raises exceptions.
        """
        if not narrative or not narrative.strip():
            return None

        try:
            tier_names = ", ".join(t.name for t in RenderTier)
            prompt = _PROMPT_TEMPLATE.format(tiers=tier_names, narrative=narrative)

            # Use claude CLI subprocess (Claude Max subscription)
            # Sonnet is sufficient for visual extraction — faster, cheaper
            cmd = [
                "claude",
                "--setting-sources", "user",
                "-p",
                prompt,
                "--output-format", "json",
                "--model", "sonnet",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_RESPONSE_TIMEOUT,
            )

            if proc.returncode != 0:
                return None

            envelope = json.loads(stdout)
            text = envelope.get("result", "")

            if not text:
                return None

            # Handle markdown-wrapped JSON
            md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if md_match:
                text = md_match.group(1)

            data = json.loads(text)

            # Validate required fields
            if not _REQUIRED_FIELDS.issubset(data.keys()):
                return None

            if data["subject"] is None:
                return None

            # Validate tier
            if data["tier"] not in _VALID_TIER_NAMES and data["tier"] not in _VALID_TIER_VALUES:
                return None

            # Validate mood
            if data["mood"] not in VALID_MOODS:
                return None

            # Truncate subject — keep subjects concise
            if len(data["subject"]) > 100:
                data["subject"] = data["subject"][:100]

            # Filter invalid tags
            data["tags"] = [t for t in data["tags"] if t in VALID_TAGS]

            return data

        except Exception:
            logger.debug("Subject extraction failed", exc_info=True)
            return None
