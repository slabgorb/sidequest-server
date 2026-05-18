"""AudioInterpreter — extract AudioCues from narrative text.

Story 5-2: Keyword-based extraction of mood/music and SFX cues,
constrained by the genre pack's AudioConfig.
"""

from __future__ import annotations

import re

from sidequest.audio.models import AudioCue, AudioLane, MoodCategory
from sidequest.genre.models import AudioConfig

# Mood keywords: mood_value -> (keywords, base_intensity)
_MOOD_KEYWORDS: dict[MoodCategory, tuple[list[str], float]] = {
    MoodCategory.COMBAT: (
        [
            "battle",
            "fight",
            "attack",
            "sword",
            "axe",
            "charge",
            "slash",
            "strike",
            "combat",
            "war",
            "clash",
            "swords",
            "fray",
            "swarm",
            "dragon",
            "flame",
            "roar",
            "explosion",
            "blast",
            "punch",
        ],
        0.8,
    ),
    MoodCategory.TAVERN: (
        [
            "tavern",
            "inn",
            "bar",
            "ale",
            "drink",
            "hearth",
            "laughter",
            "roasting",
            "mead",
            "jazz",
            "piano",
            "smoke",
            "whiskey",
            "cabaret",
            "speakeasy",
            "cocktail",
            "glass",
            "barman",
            "bartender",
            "trumpet",
            "saxophone",
            "singer",
            "band",
            "trio",
            "music",
        ],
        0.4,
    ),
    MoodCategory.EXPLORATION: (
        [
            "trail",
            "wander",
            "journey",
            "forest",
            "path",
            "explore",
            "travel",
            "oaks",
            "birdsong",
            "stream",
            "mossy",
            "wasteland",
            "dust",
            "horizon",
        ],
        0.3,
    ),
    MoodCategory.TENSION: (
        [
            "shadow",
            "shadows",
            "dark",
            "darkness",
            "growl",
            "flicker",
            "creep",
            "danger",
            "ominous",
            "dread",
            "lurk",
            "radiation",
            "mutant",
            "contaminated",
        ],
        0.6,
    ),
    MoodCategory.MYSTERY: (
        ["mysterious", "mystery", "strange", "whisper", "enigma", "riddle"],
        0.4,
    ),
    MoodCategory.SORROW: (
        ["sorrow", "grief", "mourn", "weep", "loss", "lament", "tears"],
        0.4,
    ),
    MoodCategory.TRIUMPH: (
        ["victory", "triumph", "glory", "conquer", "celebrate", "won"],
        0.7,
    ),
    MoodCategory.RITUAL: (
        ["ritual", "chant", "ceremony", "altar", "invoke", "summon"],
        0.5,
    ),
    MoodCategory.SETTLEMENT: (
        [
            "settlement",
            "camp",
            "village",
            "market",
            "trade",
            "barter",
            "shelter",
            "outpost",
            "home",
            "gather",
            "community",
        ],
        0.4,
    ),
    MoodCategory.RUINS: (
        [
            "ruins",
            "rubble",
            "collapsed",
            "crumbling",
            "desolate",
            "wreckage",
            "abandoned",
            "decay",
            "overgrown",
        ],
        0.5,
    ),
    MoodCategory.REST: (
        [
            "rest",
            "sleep",
            "campfire",
            "calm",
            "quiet",
            "peaceful",
            "dawn",
            "stars",
            "blanket",
            "dream",
        ],
        0.3,
    ),
}

# SFX keyword patterns: sfx_id -> keywords to search for
_SFX_KEYWORDS: dict[str, list[str]] = {
    # Fantasy
    "sword_clash": ["sword", "swords", "clash", "blade"],
    "door_creak": ["door creak", "door open", "creaks open"],
    "thunder": ["thunder"],
    "footsteps": ["footstep", "footsteps"],
    "fire_crackle": ["fire crackle", "fire crack", "crackle"],
    "arrow_loose": ["arrow", "bow"],
    "coin_drop": ["coin", "coins"],
    # Wasteland / sci-fi
    "explosion": ["explosion", "explode", "detonation", "blast"],
    "explosion_heavy": ["massive explosion", "erupts", "devastat"],
    "metal_impact": ["metal", "clang", "impact", "smash"],
    "metal_clang": ["clang", "metal ring"],
    "energy_weapon": ["energy beam", "laser", "plasma", "energy weapon"],
    "energy_pistol": ["pistol", "sidearm", "energy shot"],
    "zap": ["zap", "electric", "shock", "spark"],
    "energy_field": ["energy field", "force field", "radiation", "pulse"],
    "mutant_ooze": ["ooze", "slime", "mutant", "goo"],
    "computer_noise": ["computer", "terminal", "console", "beep", "hack"],
    "scavenge": ["scavenge", "scrap", "loot", "salvage", "rummage"],
    "tin_rattle": ["tin", "rattle", "cans", "junk"],
    "door_open": ["door open", "hatch", "airlock"],
    "door_close": ["door close", "door shut", "slam shut"],
    "blade_slash": ["slash", "cut", "slice", "machete"],
    "punch": ["punch", "fist", "pummel", "brawl"],
    "thruster": ["thruster", "engine", "rocket", "vehicle"],
    "machinery_hum": ["machinery", "generator", "hum", "turbine"],
}

# Intensity boosters — words that push intensity higher
_INTENSITY_BOOSTERS = [
    "dragon",
    "flame",
    "roar",
    "unleash",
    "torrent",
    "sear",
    "crumble",
    "explode",
    "fury",
    "rage",
    "devastat",
]


class AudioInterpreter:
    """Extracts AudioCues from narrative text using genre-pack AudioConfig."""

    def interpret(self, narrative: str, audio_config: AudioConfig) -> list[AudioCue]:
        """Extract audio cues from narrative text.

        Args:
            narrative: The narrative text to analyse.
            audio_config: Genre pack audio configuration constraining output.

        Returns:
            List of AudioCue objects suitable for AudioMixer consumption.
        """
        if not narrative or not narrative.strip():
            return []

        text = narrative.lower()
        cues: list[AudioCue] = []

        # --- Music / mood cue (at most one) ---
        # ADR-033 Pillar 3: an alias-only genre mood (declared in
        # mood_aliases but not mood_tracks) is still classifiable from
        # prose — the LibraryBackend resolves it through the alias chain
        # at track-selection time.
        available_moods = set(audio_config.mood_tracks.keys()) | set(
            audio_config.mood_aliases.keys()
        )
        best_mood: str | None = None
        best_score = 0
        best_intensity = 0.5

        # Built-in keyword map (enum-keyed)
        for mood, (keywords, base_intensity) in _MOOD_KEYWORDS.items():
            if mood.value not in available_moods:
                continue
            score = sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", text))
            if score > best_score:
                best_score = score
                best_mood = mood.value
                best_intensity = base_intensity

        # Genre-specific keyword map (string-keyed) — overrides built-ins on higher score
        genre_keywords = getattr(audio_config, "mood_keywords", {})
        for mood_str, keywords in genre_keywords.items():
            if mood_str not in available_moods:
                continue
            score = sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", text))
            if score > best_score:
                best_score = score
                best_mood = mood_str
                best_intensity = 0.5

        # Fall back to "exploration" when no keywords match — ensures music
        # starts even during intro/creation scenes with no combat/tavern words
        if best_score < 1 and "exploration" in available_moods:
            best_mood = "exploration"
            best_intensity = 0.3

        if best_mood is not None:
            intensity = best_intensity
            # Boost intensity for dramatic words
            boost = sum(1 for b in _INTENSITY_BOOSTERS if re.search(rf"\b{re.escape(b)}\b", text))
            intensity = min(1.0, intensity + boost * 0.1)
            cues.append(
                AudioCue(
                    lane=AudioLane.MUSIC,
                    mood=best_mood,
                    intensity=intensity,
                )
            )

        # --- SFX cues ---
        available_sfx = set(audio_config.sfx_library.keys())
        for sfx_id, keywords in _SFX_KEYWORDS.items():
            if sfx_id not in available_sfx:
                continue
            if any(kw in text for kw in keywords):
                cues.append(
                    AudioCue(
                        lane=AudioLane.SFX,
                        sfx_id=sfx_id,
                        intensity=0.7,
                    )
                )

        return cues
