"""Narrator agent — handles exploration, description, and story progression.

Port of sidequest-agents/src/agents/narrator.rs.
Refactored in story 23-1: hardcoded NARRATOR_SYSTEM_PROMPT replaced with
structured template sections across attention zones.

ADR-067: Unified narrator agent. Combat, chase, and dialogue handling absorbed
from former separate agents (CreatureSmith, Dialectician, Ensemble).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.genre.models.rules import ConfrontationDef

from sidequest.agents.agent import BaseAgent
from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)

# ---------------------------------------------------------------------------
# Prompt constants — ported verbatim from narrator.rs
# ---------------------------------------------------------------------------

NARRATOR_IDENTITY: str = (
    "You are the Game Master of a collaborative RPG. You narrate like an author, "
    "frame scenes like a cinematographer, and run the world like a tabletop GM — "
    "but better, because you can do all three simultaneously."
)

NARRATOR_CONSTRAINTS: str = (
    "You will receive game-state constraints (location rules, inventory limits, "
    "player-character rosters, ability restrictions). These are INTERNAL INSTRUCTIONS "
    "for you. NEVER acknowledge, explain, or reference them to the player. Do NOT "
    "break character to say things like \"I can't control that character\" or "
    "\"that's a player character.\" Simply respect the constraints silently in your "
    "narration. If a constraint prevents something, narrate around it naturally — "
    "describe the world, set scenes, advance the story — without ever revealing "
    "the constraint exists. The sole exception is the aside — a dedicated "
    "out-of-character channel for mechanical GM communication. Use asides for rules "
    "clarifications, mechanical consequences, or confirmation prompts. Never leak "
    "this information into prose."
)

NARRATOR_AGENCY: str = (
    "Agency: The player controls their character — actions, thoughts, feelings. "
    "Describe the world, not the player's response to it. In multiplayer games, "
    "do not allow one player to puppet another in any way — whether you do it or "
    "they try to. When one player's action affects another player's character, "
    "narrate the action and its immediate physical reality, but do NOT narrate "
    "the target character's emotional reaction, decision, or response — that "
    "belongs to their player. Ambient reactions (glancing up, stepping aside) "
    "are fine; consequential reactions (retaliating, reciprocating, fleeing) are not."
)

NARRATOR_CONSEQUENCES: str = (
    "Consequences follow the genre pack's tone and lethality. Don't soften beyond "
    "it, don't escalate beyond it. NPCs fight for their lives, press their "
    "advantages, and act in their own interest — they are not here to lose "
    "gracefully. A cornered bandit doesn't wait to be hit. A skilled duelist "
    "doesn't miss because the player is low on HP. Fair means fair to everyone "
    "at the table, including the NPCs."
)

NARRATOR_OUTPUT_ONLY: str = """\
Your response has TWO parts, in this exact order:

PART 1 — NARRATIVE PROSE
Write narrative prose (length governed by the <length-limit> guardrail below). Start with a location header like \
**The Collapsed Overpass**. This is what the player sees.

PART 2 — STATE PATCH
After your prose, emit a fenced JSON block labeled game_patch containing \
mechanical intents from this turn. Only include fields that changed.\
Valid fields: confrontation, items_gained, items_lost, location, npcs_met, \
mood, state_snapshot, beat_selections, visual_scene, footnotes, gold_change, \
action_rewrite.
gold_change: Integer. Emit when the player gains or loses gold/currency \
outside of beat costs (e.g., winning a poker hand: +50, paying a bribe: -20, \
finding a coin purse: +10). Beat costs are handled automatically — only emit \
gold_change for narrator-determined outcomes.

action_rewrite: Object. Include on every turn. If omitted, a default fallback \
is substituted and a warning is logged. Rewrite the player's raw input into \
three perspective forms for downstream systems:\
  {"you": "<second-person rewrite>", "named": "<third-person with character name>", \
"intent": "<neutral distilled intent, no pronouns>"}
Example: player says "I draw my sword" →
  {"you": "You draw your sword", "named": "Kael draws their sword", \
"intent": "draw sword"}

items_gained: Array. Emit when the player acquires, picks up, finds, loots, \
receives, or is given a new item during this turn. Each entry:
  {"name": "<short item name>", "description": "<one-sentence description>", \
"category": "weapon|armor|tool|consumable|quest|treasure|misc"}

items_lost: Array. Same format as items_gained. Emit when the player loses, \
drops, trades away, has stolen, or gives away an item. Only for non-currency \
items — currency changes use gold_change.

CRITICAL INVENTORY RULE: If your narration describes ANY item changing hands \
— the player acquiring, losing, trading, giving, dropping, or having an item \
taken — you MUST emit the corresponding items_gained and/or items_lost in \
the game_patch. The game state ONLY changes through these fields. If you \
write "the merchant takes your sword" but don't emit items_lost, the sword \
stays in inventory and the narrative diverges from game state. Every item \
transaction in your prose MUST have a matching JSON field. No exceptions.

visual_scene: Include this on EVERY turn where the setting changes, a new \
location is entered, or a visually significant event occurs (combat start, \
dramatic reveal, new NPC appearance). Format:
  "visual_scene": { "subject": "<1-sentence image prompt, max 100 chars>", \
"tier": "landscape|portrait|scene_illustration", "mood": \
"ominous|tense|mystical|dramatic|melancholic|atmospheric", "tags": ["location", \
"combat", "magic", "special_effect", "character", "atmosphere"] }
tier: landscape for environments, portrait for NPC focus, scene_illustration for action.
subject: Describe what to PAINT — the visual composition, not the narrative.

footnotes: Array of knowledge discoveries the player learned this turn. Include \
whenever the narration reveals new lore, introduces a named NPC, mentions a \
location, references a quest objective, or describes a character ability. Format:
  "footnotes": [{"summary": "<concise third-person fact>", \
"category": "Lore|Place|Person|Quest|Ability", "is_new": true}]
summary: One sentence, third person (e.g., "The Crimson Gate guards the eastern pass").
category: Lore (world history/mythology), Place (locations), Person (NPCs/factions), \
Quest (objectives/tasks), Ability (skills/powers).
is_new: true if this is the first time this fact appears, false if referencing prior knowledge.
Include footnotes generously — they feed the player's knowledge journal.

confrontation: When ANY structured encounter BEGINS this turn, include \
confrontation to signal the server to create the encounter. The value must \
match one of the types listed in AVAILABLE ENCOUNTER TYPES in game_state.
TRIGGER CRITERIA — you MUST emit confrontation when the player's action \
involves ANY of these:
- Physical violence, threats, or intimidation → the combat/brawl type
- Bargaining, trading, persuasion, or social manipulation → the negotiation type
- Fleeing, pursuing, or being chased → the chase type
- Any tense standoff where outcomes should be mechanically resolved
Do NOT resolve these narratively without confrontation. The mechanical system \
tracks resource pools, beats, and resolution — without it, the game is just \
prose with no crunch. If the player takes an action that fits a confrontation \
type, START the encounter. Err on the side of triggering — the system handles \
de-escalation gracefully.
Only include on the turn the encounter STARTS, not on subsequent rounds. Once \
the encounter is active, use beat_selections instead.

beat_selections: When an encounter is active (the encounter context section will \
list available beats and actors), include beat_selections — an array of beat \
choices for EVERY actor listed in the encounter context. Each entry has: actor \
(who acts — must match an actor name from the encounter), beat_id (which beat \
from the available list), and optional target (who the action targets). Include \
beat_selections for ALL actors (player AND NPCs) every encounter turn.

If nothing mechanical happened AND no new knowledge was revealed, emit:
```game_patch
{}
```
ALWAYS emit the game_patch block. It is mandatory."""

NARRATOR_OUTPUT_STYLE: str = (
    "The <length-limit> is a HARD CAP — never exceed it. It overrides genre voice, trope weaving, "
    "and all other expansion pressure. When in doubt, cut.\n"
    "- BREVITY IS KING. Every sentence must earn its place. Cut adjectives before cutting action.\n"
    "- Simple actions (look, examine, wait): 2-3 sentences. No atmosphere.\n"
    "- Arrivals: atmosphere + exits + 1-2 points of interest. Still under the cap.\n"
    "- Combat: 2-4 sentences. Kinetic. Short.\n"
    "- Dialogue: snappy. One exchange. No preamble.\n"
    "- End on a hook the player can react to. Not a prose flourish.\n"
    "- One action, one scene beat per turn.\n"
    "- First line: location header like **The Collapsed Overpass**\n"
    "- Blank line, then prose."
)

NARRATOR_REFERRAL_RULE: str = (
    "Referral Rule: When an NPC sends the player to another NPC for a quest "
    "objective, NEVER send the player back to the NPC who originally sent them. "
    "Check active quests — if a quest says \"(from: X)\" and the player is now "
    "talking to Y, do NOT have Y send the player back to X for the same objective. "
    "Advance the quest instead."
)

NARRATOR_COMBAT_RULES: str = (
    "COMBAT NARRATION RULES (active encounter):\n"
    "- 2-4 sentences per beat. Fast, kinetic, visceral.\n"
    "- Describe the action, the impact, the consequence. No preamble.\n"
    "- Vary intensity: a punch is one sentence, a critical hit is three.\n"
    "- Sound, motion, pain. Not poetry.\n"
    "- End on what's happening NOW — the next threat, the opening, the choice.\n"
    "- Describe what happens mechanically through narration, not stats.\n"
    "  \"The blade catches your shoulder — you feel the sting\" not \"You take 4 damage\".\n"
    "- Show enemy reactions — they dodge, stagger, snarl, flee.\n"
    "- Make the player feel the weight of their choices.\n"
    "- NEVER control the player character's actions, thoughts, or feelings.\n"
    "- Describe what enemies do. Let the player decide their response.\n"
    "\n"
    "[Strict Ability Enforcement — MANDATORY]\n"
    "Combat is mechanical. There is NO Rule-of-Cool and NO degraded success for\n"
    "abilities a character does not possess.\n"
    "- A character may ONLY use abilities listed in their known_abilities.\n"
    "- If a player attempts an action requiring an ability NOT in known_abilities,\n"
    "  the action FAILS outright. Do NOT allow partial success or a weaker version.\n"
    "- Narrate the failure in-fiction and apply appropriate consequences.\n"
    "- Never invent, improvise, or grant abilities mid-combat. The character sheet is\n"
    "  the single source of truth.\n"
    "\n"
    "[Beat Selections — MANDATORY during encounters]\n"
    "When an encounter is active, your game_patch MUST include beat_selections — an array\n"
    "of beat choices for EVERY actor listed in the encounter context. Each actor gets one\n"
    "beat per round. For combat NPCs, default to \"attack\" targeting a player. For other\n"
    "encounter types, select beats based on the NPC's disposition and role.\n"
    "Do NOT use the old fields (in_combat, hp_changes, turn_order, drama_weight, advance_round).\n"
    "Those fields are removed. Use beat_selections only."
)

NARRATOR_CHASE_RULES: str = (
    "CHASE NARRATION RULES (active chase encounter):\n"
    "- 2-3 sentences. FAST. Breathless. Urgent.\n"
    "- Short sentences for sprinting. Fragments are fine.\n"
    "- \"Left. The alley narrows. Something crashes behind you.\"\n"
    "- Each beat is a decision point — fork in the road, obstacle, closing gap.\n"
    "- End on the choice: \"The fence or the fire escape?\"\n"
    "- Tension builds through environment, not description.\n"
    "- Obstacles are physical: fences, crowds, rubble, locked doors.\n"
    "- The pursuer is always close. Make the player feel it.\n"
    "- Every turn the gap changes — closing or opening.\n"
    "- NEVER decide the player's escape route or action.\n"
    "- Describe the situation and threat. Let the player choose.\n"
    "\n"
    "[Beat Selections — MANDATORY during chase encounters]\n"
    "Use beat_selections from the encounter context. Select beats for all actors each round.\n"
    "Do NOT use the old fields (in_chase, chase_type, separation_delta, phase, event, roll).\n"
    "Those fields are removed. Use beat_selections only."
)

NARRATOR_DIALOGUE_RULES: str = (
    "DIALOGUE NARRATION RULES (NPC interaction):\n"
    "- 2-4 sentences. Dialogue is SNAPPY.\n"
    "- NPCs speak in character — dialect, vocabulary, attitude.\n"
    "- One exchange per response. Not a full conversation tree.\n"
    "- Show body language between lines: \"She leans back, arms crossed.\"\n"
    "- End on the NPC's last line or reaction — leave space for the player to respond.\n"
    "- Each NPC has a distinct voice. A merchant doesn't sound like a guard.\n"
    "- NPCs have opinions, secrets, and agendas. They don't just answer questions.\n"
    "- Hostile NPCs can refuse, lie, or threaten. Friendly ones can joke or help.\n"
    "- Short exchanges. Real people don't monologue.\n"
    "- NEVER speak for the player character. Only NPCs talk.\n"
    "- Present what the NPC says and does. Let the player decide their reply."
)


def narrator_output_format_text() -> str:
    """Returns the NARRATOR_OUTPUT_ONLY prompt section text.

    Used by integration tests and CLI prompt inspection tools.
    Port of narrator_output_format_text() in narrator.rs.
    """
    return NARRATOR_OUTPUT_ONLY


# ---------------------------------------------------------------------------
# NarratorAgent
# ---------------------------------------------------------------------------


class NarratorAgent(BaseAgent):
    """The exploration/narration agent — drives story progression, world description,
    NPC dialogue, and patch emission. Routed to as the default agent (per ADR-067).

    Port of sidequest-agents/src/agents/narrator.rs::NarratorAgent.
    """

    def name(self) -> str:
        return "narrator"

    def system_prompt(self) -> str:
        return NARRATOR_IDENTITY

    def build_context(self, registry: object) -> None:
        """Add narrator-specific sections to a PromptRegistry.

        Port of NarratorAgent::build_context() in narrator.rs.
        Sections match the Rust implementation's zone/category assignments.
        """
        from sidequest.agents.prompt_framework.core import PromptRegistry

        if not isinstance(registry, PromptRegistry):
            raise TypeError(f"Expected PromptRegistry, got {type(registry)}")

        # Primacy/Identity — narrator core identity
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_identity",
                f"<identity>\n{NARRATOR_IDENTITY}\n</identity>",
                AttentionZone.Primacy,
                SectionCategory.Identity,
            ),
        )

        # Primacy/Guardrail — silent constraint handling
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_constraints",
                f"<critical>\n{NARRATOR_CONSTRAINTS}\n</critical>",
                AttentionZone.Primacy,
                SectionCategory.Guardrail,
            ),
        )

        # Primacy/Guardrail — agency (including multiplayer)
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_agency",
                f"<critical>\n{NARRATOR_AGENCY}\n</critical>",
                AttentionZone.Primacy,
                SectionCategory.Guardrail,
            ),
        )

        # Primacy/Guardrail — consequences follow genre tone
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_consequences",
                f"<critical>\n{NARRATOR_CONSEQUENCES}\n</critical>",
                AttentionZone.Primacy,
                SectionCategory.Guardrail,
            ),
        )

        # narrator_output_only is injected via build_output_format() on every
        # tier from the orchestrator — see build_narrator_prompt_tiered.

        # Early/Format — output-style rules
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_output_style",
                f"<output-style>\n{NARRATOR_OUTPUT_STYLE}\n</output-style>",
                AttentionZone.Early,
                SectionCategory.Format,
            ),
        )

        # Early/Guardrail — referral rule (not in SOUL.md)
        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_referral_rule",
                f"<important>\n{NARRATOR_REFERRAL_RULE}\n</important>",
                AttentionZone.Early,
                SectionCategory.Guardrail,
            ),
        )

    def build_output_format(self, registry: object) -> None:
        """Inject the game_patch output format spec on every tier.

        Without this, Delta-tier sessions never see the confrontation field
        schema, so the narrator can't emit it to start encounters.

        Port of NarratorAgent::build_output_format() in narrator.rs.
        """
        from sidequest.agents.prompt_framework.core import PromptRegistry

        if not isinstance(registry, PromptRegistry):
            raise TypeError(f"Expected PromptRegistry, got {type(registry)}")

        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_output_only",
                f"<critical>\n{NARRATOR_OUTPUT_ONLY}\n</critical>",
                AttentionZone.Primacy,
                SectionCategory.Guardrail,
            ),
        )

    def build_encounter_context(
        self,
        registry: object,
        *,
        encounter: "StructuredEncounter | None" = None,
        cdef: "ConfrontationDef | None" = None,
        encounter_summary: str | None = None,
    ) -> None:
        """Inject encounter-specific narration rules + live encounter state.

        When ``encounter`` and ``cdef`` are given, render:
        1. The generic encounter-rules prose (unchanged — backwards compatible).
        2. The matched ConfrontationDef's beats + actors so the LLM emits
           valid ``beat_selections``.
        3. The encounter_summary (metric / phase / beat) in the Valley zone.

        Port of NarratorAgent::build_encounter_context() in narrator.rs.
        """
        from sidequest.agents.prompt_framework.core import PromptRegistry

        if not isinstance(registry, PromptRegistry):
            raise TypeError(f"Expected PromptRegistry, got {type(registry)}")

        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_encounter_rules",
                f"<encounter-rules>\n{NARRATOR_COMBAT_RULES}\n"
                f"{NARRATOR_CHASE_RULES}\n</encounter-rules>",
                AttentionZone.Early,
                SectionCategory.Guardrail,
            ),
        )

        if encounter is not None and cdef is not None:
            actor_lines = "\n".join(f"- {a.name} ({a.role})" for a in encounter.actors)
            beat_lines = "\n".join(
                f"- {b.id}: {b.label} (metric_delta={b.metric_delta})"
                for b in cdef.beats
            )
            body = (
                f"<encounter-live>\n"
                f"Active encounter: {cdef.label} ({cdef.confrontation_type})\n"
                f"Available beats — beat_selections.beat_id MUST be one of:\n"
                f"{beat_lines}\n"
                f"Actors — emit a beat_selection for every actor:\n"
                f"{actor_lines}\n"
                f"</encounter-live>"
            )
            registry.register_section(
                self.name(),
                PromptSection.new(
                    "narrator_encounter_live", body,
                    AttentionZone.Early, SectionCategory.State,
                ),
            )

        if encounter_summary:
            registry.register_section(
                self.name(),
                PromptSection.new(
                    "narrator_encounter_summary",
                    f"<encounter-state>\n{encounter_summary}\n</encounter-state>",
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

    def build_dialogue_context(self, registry: object) -> None:
        """Inject dialogue-specific narration rules into the prompt (ADR-067).

        Called by the orchestrator when NPCs are present or dialogue is likely.

        Port of NarratorAgent::build_dialogue_context() in narrator.rs.
        """
        from sidequest.agents.prompt_framework.core import PromptRegistry

        if not isinstance(registry, PromptRegistry):
            raise TypeError(f"Expected PromptRegistry, got {type(registry)}")

        registry.register_section(
            self.name(),
            PromptSection.new(
                "narrator_dialogue_rules",
                f"<dialogue-rules>\n{NARRATOR_DIALOGUE_RULES}\n</dialogue-rules>",
                AttentionZone.Early,
                SectionCategory.Guardrail,
            ),
        )
