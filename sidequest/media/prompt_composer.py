"""PromptComposer — genre pack style blocks, visual tags, negative prompts.

Composes positive/negative image generation prompts by combining StageCue data
with VisualStyle configuration from genre packs.
"""

from __future__ import annotations

import hashlib
import logging

from pydantic import BaseModel

from sidequest.genre.models import VisualStyle
from sidequest.renderer.models import RenderTier, StageCue

_TIER_PROMPT_PREFIX: dict[RenderTier, str] = {
    RenderTier.TACTICAL_SKETCH: "top-down tactical battle map, square grid overlay, each combatant marked with a bold letter initial inside a colored circle, clear spacing between tokens, clean flat illustration style, high contrast labels, bird's-eye view, no perspective",
    RenderTier.LANDSCAPE: "wide establishing shot, scenic vista, atmospheric",
    RenderTier.PORTRAIT: "character portrait, detailed face and attire, centered subject",
    RenderTier.SCENE_ILLUSTRATION: "",
}

# Default visual tags for common location keywords.
_DEFAULT_LOCATION_TAGS: dict[str, str] = {
    "tavern": "wooden beams, hearth fire, ale-stained tables, smoky interior",
    "forest": "dense canopy, dappled sunlight, mossy undergrowth, ancient trees",
    "dungeon": "stone corridors, flickering torches, damp walls, iron grates",
    "castle": "stone battlements, tapestries, vaulted ceilings, heraldic banners",
    "market": "merchant stalls, colorful awnings, crowded square, barrels and crates",
    "cave": "stalactites, dim glow, rough stone, underground pools",
    "temple": "stained glass, marble columns, incense smoke, sacred altars",
    "battlefield": "scorched earth, broken weapons, banners in wind, smoke and dust",
}

# Negatives always included regardless of genre.
_BASE_NEGATIVES = "watermark, signature, text, blurry, deformed, extra limbs, modern clothing, contemporary, t-shirt, collared shirt, photograph"

# Tiers that force the Flux worker (text rendering / cartography).
_FLUX_FORCED_TIERS = {RenderTier.TEXT_OVERLAY, RenderTier.CARTOGRAPHY, RenderTier.TACTICAL_SKETCH}

# T5-XXL token limit — Flux uses T5-XXL which supports 512 tokens
# and understands rich literary vocabulary natively.
_TOKEN_LIMIT = 512

# Rough estimate for token budgeting without importing the actual tokenizer.
_TOKENS_PER_WORD = 1.3

log = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Estimate token count from whitespace-delimited word count."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * _TOKENS_PER_WORD))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens tokens."""
    words = text.split()
    max_words = int(max_tokens / _TOKENS_PER_WORD)
    if max_words <= 0:
        return ""
    return " ".join(words[:max_words])


class ComposedPrompt(BaseModel):
    """Output of prompt composition — everything a worker needs."""

    positive_prompt: str
    negative_prompt: str
    clip_prompt: str
    worker_type: str
    seed: int


class PromptComposer:
    """Composes image-generation prompts from StageCue + VisualStyle."""

    # Minimum tokens reserved for narrative (subject, mood, etc.)
    _MIN_NARRATIVE_TOKENS = 40

    def __init__(
        self,
        visual_tag_overrides: dict[str, str] | None = None,
        *,
        location_weight: float = 1.0,
        subject_weight: float = 1.0,
    ) -> None:
        self._tag_overrides = visual_tag_overrides or {}
        self.weights = {
            "location": max(0.0, min(1.0, location_weight)),
            "subject": max(0.0, min(1.0, subject_weight)),
        }

    def compose(self, cue: StageCue, style: VisualStyle) -> ComposedPrompt:
        positive = self._build_positive(cue, style)
        clip = self._build_clip(cue, style)
        negative = self._build_negative(cue, style)
        worker = self._select_worker(cue, style)
        seed = self._derive_seed(cue, style)
        return ComposedPrompt(
            positive_prompt=positive,
            negative_prompt=negative,
            clip_prompt=clip,
            worker_type=worker,
            seed=seed,
        )

    def _build_positive(self, cue: StageCue, style: VisualStyle) -> str:
        tier_prefix = _TIER_PROMPT_PREFIX.get(cue.tier, "")

        # Style suffix: placed AFTER narrative so subject gets positional
        # priority.  Trimmable to guarantee narrative budget.
        style_suffix = style.positive_suffix or ""

        tier_tokens = _estimate_tokens(tier_prefix) if tier_prefix else 0

        # --- Narrative parts (trimmed if over budget) ---
        narrative_parts: list[str] = []
        if cue.subject:
            narrative_parts.append(cue.subject)
        if cue.mood:
            narrative_parts.append(cue.mood)
        if cue.location:
            narrative_parts.append(self._resolve_location_tags(cue.location))
        if cue.characters:
            narrative_parts.append(", ".join(cue.characters))
        if cue.tags:
            narrative_parts.append(", ".join(cue.tags))

        # For portrait renders, reinforce character distinctiveness.
        # Appended last so it's the first thing dropped under budget pressure.
        if cue.tier == RenderTier.PORTRAIT:
            narrative_parts.append(
                "solo character, detailed distinctive features, unique appearance"
            )

        # Count parts for join overhead (commas + spaces between parts).
        part_count = (1 if tier_prefix else 0) + len(narrative_parts) + (1 if style_suffix else 0)
        join_overhead = part_count * 1
        available = _TOKEN_LIMIT - tier_tokens - join_overhead

        # Cap style to guarantee narrative minimum budget.
        style_tokens = _estimate_tokens(style_suffix)
        if style_suffix and available - style_tokens < self._MIN_NARRATIVE_TOKENS:
            max_style = max(0, available - self._MIN_NARRATIVE_TOKENS)
            if max_style > 0:
                style_suffix = _truncate_to_tokens(style_suffix, max_style)
                style_tokens = _estimate_tokens(style_suffix)
            else:
                style_suffix = ""
                style_tokens = 0

        narrative_budget = available - style_tokens

        kept: list[str] = []
        used = 0
        for part in narrative_parts:
            part_tokens = _estimate_tokens(part)
            if used + part_tokens <= narrative_budget:
                kept.append(part)
                used += part_tokens
            else:
                # Try to fit a truncated version of this part
                remaining = narrative_budget - used
                if remaining > 2:
                    truncated = _truncate_to_tokens(part, remaining)
                    if truncated:
                        kept.append(truncated)
                        used += _estimate_tokens(truncated)
                log.debug(
                    "Token budget: trimmed narrative from %d to %d tokens "
                    "(style capped to preserve character detail)",
                    sum(_estimate_tokens(p) for p in narrative_parts),
                    used,
                )
                break  # remaining parts are lower priority — drop them

        # Assemble order depends on tier:
        # - PORTRAIT: tier + narrative + style (character subject leads)
        # - Other tiers: style + tier + narrative (genre atmosphere leads)
        final: list[str] = []
        if cue.tier == RenderTier.PORTRAIT:
            if tier_prefix:
                final.append(tier_prefix)
            final.extend(kept)
            if style_suffix:
                final.append(style_suffix)
        else:
            if style_suffix:
                final.append(style_suffix)
            if tier_prefix:
                final.append(tier_prefix)
            final.extend(kept)

        return ", ".join(final)

    def _build_clip(self, cue: StageCue, style: VisualStyle) -> str:
        """Build CLIP encoder prompt — short style/aesthetic keywords.

        CLIP (clip_l) understands artistic style, medium, lighting, and mood
        keywords. Keep this short and tag-like; detailed content goes to T5.
        """
        parts: list[str] = []
        tier_prefix = _TIER_PROMPT_PREFIX.get(cue.tier, "")
        if tier_prefix:
            parts.append(tier_prefix)
        if style.positive_suffix:
            parts.append(style.positive_suffix)
        if cue.mood:
            parts.append(cue.mood)
        return ", ".join(parts)

    def _resolve_location_tags(self, location: str) -> str:
        loc_lower = location.lower()
        tags: str | None = None

        # Check overrides first.
        for key, tag_str in self._tag_overrides.items():
            if key in loc_lower:
                tags = tag_str
                break

        # Check default tags.
        if tags is None:
            for key, tag_str in _DEFAULT_LOCATION_TAGS.items():
                if key in loc_lower:
                    tags = tag_str
                    break

        if tags is None:
            return location

        # Apply location weight — truncate tag list when weight < 1.0
        weight = self.weights.get("location", 1.0)
        if weight < 1.0:
            parts = [p.strip() for p in tags.split(",")]
            keep = max(1, int(len(parts) * weight))
            return ", ".join(parts[:keep])

        return tags

    def _build_negative(self, cue: StageCue, style: VisualStyle) -> str:
        parts: list[str] = [_BASE_NEGATIVES]

        if style.negative_prompt:
            parts.append(style.negative_prompt)

        if cue.tier == RenderTier.TACTICAL_SKETCH:
            parts.append("illegible text, blurry labels, overlapping tokens, 3D perspective, realistic rendering, photographic")
        elif cue.tier == RenderTier.SCENE_ILLUSTRATION:
            parts.append("cluttered, messy composition")

        return ", ".join(parts)

    def _select_worker(self, cue: StageCue, style: VisualStyle) -> str:
        if cue.tier in _FLUX_FORCED_TIERS:
            return "flux"
        return style.preferred_model

    def _derive_seed(self, cue: StageCue, style: VisualStyle) -> int:
        key_parts = [
            cue.subject,
            cue.tier.value,
            cue.location,
            "|".join(sorted(cue.characters)),
        ]
        key = ":".join(key_parts)
        digest = hashlib.sha256(key.encode()).hexdigest()
        return (int(digest[:8], 16) + style.base_seed) % (2**32)
