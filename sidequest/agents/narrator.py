"""Narrator agent — handles exploration, description, and story progression.

Port of sidequest-agents/src/agents/narrator.rs.
Refactored in story 23-1: hardcoded NARRATOR_SYSTEM_PROMPT replaced with
structured template sections across attention zones.

ADR-067: Unified narrator agent. Combat, chase, and dialogue handling absorbed
from former separate agents (CreatureSmith, Dialectician, Ensemble).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sidequest.game.encounter import StructuredEncounter
    from sidequest.genre.models.character import ClassDef
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
    'break character to say things like "I can\'t control that character" or '
    '"that\'s a player character." Simply respect the constraints silently in your '
    "narration. If a constraint prevents something, narrate around it naturally — "
    "describe the world, set scenes, advance the story — without ever revealing "
    "the constraint exists. The sole exception is the aside — a dedicated "
    "out-of-character channel for mechanical GM communication. Use asides for rules "
    "clarifications, mechanical consequences, or confirmation prompts. Never leak "
    "this information into prose."
)

NARRATOR_AGENCY: str = (
    "Agency: The player controls their character — actions, thoughts, feelings, "
    "and dialogue. Describe the world, not the player's response to it. "
    "You MUST NOT put dialogue, internal thought, decisions, or new physical "
    "actions in any player character's (PC's) mouth or body that the PC's "
    "player did not declare this turn. PCs may breathe, blink, shift weight, "
    "or have their declared action's immediate physical follow-through narrated; "
    "PCs may NOT be made to speak, decide, react emotionally, or perform "
    "additional actions. NPCs may speak, react, and act freely. "
    "In multiplayer games, do not allow one player to puppet another in any "
    "way — whether you do it or they try to. When one player's action affects "
    "another player's character, narrate the action and its immediate physical "
    "reality, but do NOT narrate the target character's emotional reaction, "
    "decision, dialogue, or response — that belongs to their player. Ambient "
    "reactions (glancing up, stepping aside) are fine; consequential reactions "
    "(retaliating, reciprocating, fleeing, speaking) are not. "
    'If you would naturally write a line like "Laverne says, ..." or '
    '"Shirley nods and replies, ..." — STOP. That belongs to her player. '
    "Describe the silence, the look, the pause — let the player fill it next turn."
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
Valid fields: confrontation, items_gained, items_lost, items_discarded, \
items_consumed, \
location, npcs_met, mood, state_snapshot, beat_selections, visual_scene, \
footnotes, gold_change, action_rewrite, status_changes, \
companions_added, companions_dismissed.
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

items_lost: Array. Same format as items_gained. Emit when the player loses \
an item to the world — given away, traded, stolen, destroyed, consumed. The \
item is GONE from continuity. Only for non-currency items — currency changes \
use gold_change.

items_discarded: Array. Same format as items_gained. Emit when the player \
intentionally drops, abandons, leaves behind, or sets down an item that \
remains in the world (still potentially recoverable). Examples: "abandons \
the spear where it stands", "drops the lantern", "leaves the helmet on the \
corpse". Use items_discarded (NOT items_lost) for these — discarded items \
keep a paper-trail in inventory with state=Discarded so the player can \
narratively pick them up later. If unsure between items_lost vs \
items_discarded, prefer items_discarded — recoverability is the safer \
default.

items_consumed: Array. Same format as items_gained. Emit when the player \
USES UP a consumable — patch-foam applied, foil-strip torn open, potion \
drunk, ration eaten, charge expended. The item is GONE from inventory \
because its function was spent (distinct from items_lost which covers \
given-away/stolen/destroyed items, and from items_gained which is \
acquisition). Use items_consumed for one-shot consumables that vanish \
on use.

companions_added: Array. Emit when an NPC is hired, recruited, or otherwise \
joins the party as a companion / hireling / retainer / ally for ongoing \
travel. Each entry:
  {"name": "<companion name>", "role": "<short role e.g. torchbearer, porter, \
scout, guard>", "description": "<one-sentence panel description>", \
"notes": "<optional contract terms one-liner, may be empty>", \
"recruited_by": "<acting PC name>"}

companions_dismissed: Array of companion names (strings) being released \
from service this turn — fired, paid off, walked off, killed. The companion \
is removed from the active party roster. Use this when a hireling's \
contract ends, when they refuse a destination and walk, when morale breaks, \
or when they die.

CRITICAL COMPANION RULE: If your narration describes an NPC joining the \
party for ongoing travel ("Donut takes the contract", "the porter agrees \
to come along"), you MUST emit companions_added. If an NPC leaves the \
party for any reason, you MUST emit companions_dismissed. Without these \
fields the Party panel will not show the companion and no recruit/dismiss \
trace lands in the GM panel — the narration diverges from game state, \
exactly the same failure class as silently moving items. A passing \
shopkeeper or one-scene NPC who never leaves their post is NOT a \
companion — only emit companions_added when the NPC is genuinely joining \
the moving party.

CRITICAL INVENTORY RULE: If your narration describes ANY item changing hands \
or leaving the player's possession — acquiring, losing, trading, giving, \
dropping, abandoning, having an item taken, or USING UP a consumable — \
you MUST emit the corresponding items_gained, items_lost, items_discarded, \
or items_consumed in the game_patch. The game state ONLY changes through \
these fields. If you write "the merchant takes your sword" but don't emit \
items_lost, the sword stays in inventory and the narrative diverges from \
game state. If you write "abandons the spear" but don't emit \
items_discarded, the spear still shows state=Carried in inventory. If you \
write "you spray the last of the patch-foam" but don't emit \
items_consumed, the empty kit still shows quantity=1 in inventory. Every \
item transaction in your prose MUST have a matching JSON field. No \
exceptions.

CRITICAL LOCATION RULE: Every location header you write in PART 1 prose \
(e.g. **Vaskov Centrum — East Freight Stair**) is a scene boundary that \
the game state must track. If your prose contains ANY location header \
different from the current location, you MUST emit a `location` field in \
the game_patch set to the FINAL location header in your prose — i.e. \
where the party physically ends this turn. This applies even when one \
narration spans multiple scene cuts ("Vaskov Centrum — Corridor" → \
"Vaskov Centrum — Stairwell" → "Vaskov Centrum — Mezzanine"): emit \
location for the LAST one. The header in prose alone is NOT enough — \
without the JSON field, the location panel, the encounter-deactivation \
rule, and the lore RAG all stay anchored on the prior scene while your \
prose has moved on. This is the same class of error as describing items \
changing hands without emitting items_lost — the narration diverges from \
game state. If multiple chapter-break headers appear, emit location for \
the FINAL one only.

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

CRITICAL ADVERSARY RULE — MANDATORY when you emit confrontation: every \
adversary, enemy, creature, or antagonist NPC referenced in this turn's prose \
MUST also appear in npcs_met for the same turn with both name AND role \
populated. The server constructs the combatant list from npcs_met — if it is \
empty, the confrontation panel renders with only the player and the \
encounter is mechanically broken from the start.
- Named individual: one entry, e.g. {"name": "Gristle the Rat-King", \
"role": "hostile", "is_new": true}.
- Named group or pack: one entry with the group name, e.g. {"name": \
"Filter-leech pack", "role": "hostile", "appearance": "five keens, \
one drops a leech"}. Do NOT omit the group just because members are unnamed.
- Unnamed but distinct creature: give it a short descriptive name — \
"Ruin-rat", "Dome cultist", "Road gang leader" — whatever the prose uses.
Err on the side of including: every creature your prose describes as present \
and menacing is a combatant.

npcs_met: Array of NPC mentions from this turn's prose. Format each entry:
  {"name": "<NPC or group name>", "role": "<hostile|friendly|neutral|merchant\
|ally|patron|quest_giver|...>", "pronouns": "<she/her|he/him|they/them|it/its\
>", "appearance": "<short physical/attire note>", "is_new": true}
Only name and role are required; the rest are optional but recommended on \
first appearance. Include every named NPC, creature, or distinct group the \
player encounters, especially adversaries during a confrontation (see rule \
above).

Each entry MUST include "side": one of "player" (party allies), "opponent" \
(anyone the party is fighting), or "neutral" (bystanders, narrators, \
audience). This is structural — `role` remains free-form prose, `side` is \
a closed enum the engine routes on. Wrong sides break momentum routing.

beat_selections: When an encounter is active (the encounter context section will \
list available beats and actors), include beat_selections — an array of beat \
choices for EVERY actor listed in the encounter context. Each entry has: actor \
(who acts — must match an actor name from the encounter), beat_id (which beat \
from the available list), and optional target (who the action targets). Include \
beat_selections for ALL actors (player AND NPCs) every encounter turn.

Each beat_selection MUST include "outcome": one of "CritFail", "Fail", \
"Tie", "Success", "CritSuccess". This is the tier the prose describes — \
"Fail" if the action did not succeed, "Success" if it cleanly worked, \
"Tie" if it succeeded at a minor cost or partially, "CritSuccess" if it \
succeeded with a notable extra benefit, "CritFail" if it failed badly \
and the actor is now in a worse position than before. Match the tier to \
the prose. On dice-replay turns the engine will overwrite this from the \
actual roll.

status_changes: Array. Two entry shapes — ADD a status, or CLEAR one.

ADD shape (new lingering injury, shaken nerve, social mark, temporary buff, \
or other actor-level effect):
  {"actor": "<actor name>", "status": {"text": "<short prose label>", "severity": "Scratch|Wound|Scar|Boon"}}
- Scratch: clears at scene end (a graze, a lost composure beat).
- Wound: clears at session end or with rest (a real injury, a notable shake).
- Scar: persists until a milestone or healing event (a permanent mark — \
  reputation, broken bone, lost trust).
- Boon: temporary BENEFICIAL effect from a working, consumable, scroll, \
  potion, alien artifact, or environmental boost. Clears at scene end \
  alongside Scratch (scene-bounded by design — buffs don't trail a party \
  between encounters). Use for "Heightened Perception (3 rounds)", \
  "Steady Hand", "Vigor Surge", "Wind at our backs", any time prose \
  depicts a character GAINING something temporary rather than LOSING it. \
  Include duration in the text when prose names one ("Heightened \
  Perception (3 rounds)"); omit when prose is vague ("a moment of clarity").

CRITICAL MAGIC EFFECT RULE — MANDATORY when prose depicts a temporary \
effect taking hold from a working/consumable/scroll/potion/artifact:
If your prose says "the torchlight gets clearer", "her hands stop shaking", \
"vision sharpens", "fatigue lifts", "the air seems to thin around him", or \
any other depiction of a character's senses/body/will being temporarily \
altered by a magical or alchemical source, you MUST emit a status_changes \
ADD with severity=Boon (for beneficial alterations) or Scratch/Wound (for \
costs — a backlash, a dizzy spell). Same class of error as describing an \
item changing hands without items_lost: the prose creates an effect the \
ledger has no record of. The Boon severity is specifically for this — \
without it the player sees the prose but the system has no idea anything \
happened, and a future turn can't reference the buff coherently.

CLEAR shape (resolve / heal / escape from an existing status — emit when \
the prose explicitly describes a lingering condition lifting):
  {"actor": "<actor name>", "clear": "<status text or substring>"}
Use this when the prose says the character escapes a hold ("she wriggles \
free of the Captured grip"), recovers ("the bandage stops the Twisted \
wrist throbbing"), or shrugs off a Scratch/Wound/Scar/Boon narratively. \
Match by status text or a unique substring of it. Wound and Scar will \
NEVER auto-expire — emit a clear if the prose resolves them, or they \
persist forever and pile up on the party panel. Scratch and Boon \
auto-clear at scene end so explicit clears are optional but allowed.

Use ADDs sparingly — every status is narrative gravity. Align severity \
with how seriously the prose treats the cost (or how brightly it treats \
the buff). CLEAR aggressively when the prose resolves a condition: a \
status the narrator stops mentioning is NOT cleared by silence.

magic_working: Object. Emit when your narration depicts a character using
magic — innate psychic touch, an item firing, an alien artifact responding,
any working from the world's allowed magic sources. Format:
  "magic_working": {
    "plugin": "<one of world's active_plugins, e.g. innate_v1, item_legacy_v1>",
    "mechanism": "<one of: faction|place|time|condition|native|discovery|relational|cosmic>",
    "actor": "<character name>",
    "costs": {"<cost_type>": <0.0..1.0>, ...},
    "domain": "<one of: psychic|physical|spatial|temporal|illusory|divinatory|necromantic|elemental|transmutative|alchemical>",
    "narrator_basis": "<one-sentence why this is a working>",
    // Plugin-required fields:
    //   innate_v1: flavor (acquired|born_to_it|trained_register|covenant_lineage), consent_state (involuntary|willing)
    //   item_legacy_v1: item_id, alignment_with_item_nature (-1.0..1.0)
  }

CRITICAL MAGIC RULE — plugin-aware and proactive:
On worlds where innate_v1 appears in the magic context's active plugins,
every PC action under stress MUST consider whether reflexive flavor
surfaces. When it does, narrate the triggering stimulus (an uncanny
presence, an alien register pressing in, a sudden threat) and any
immediate physical reflex follow-through (a flinch, a recoil, a tightening
grip) — and emit magic_working with the appropriate sanity debit. Do NOT
narrate what the PC perceives, thinks, names, or feels about the
experience — internal perception and cognition belong to the player's
next turn (see NARRATOR_AGENCY). Stress-triggered surfacing is not
optional storytelling: don't wait for prose to already depict a working
before considering one — on innate-active worlds, the prose should depict
the stimulus and reflex when the world's magic system would track it.

MANDATORY for any active plugin:
If any character does something that the world's magic system would track
(psychic perception, named-gun firing with significance, alien artifact
response, etc.), you MUST emit magic_working. The system enforces hard_limits
and tracks costs against the visible ledger; describing magic in prose
without emitting magic_working is the same class of error as describing an
item changing hands without emitting items_lost — the narration diverges
from the game state. Don't describe a working you can't account for.

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
    'Check active quests — if a quest says "(from: X)" and the player is now '
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
    '  "The blade catches your shoulder — you feel the sting" not "You take 4 damage".\n'
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
    'beat per round. For combat NPCs, default to "attack" targeting a player. For other\n'
    "encounter types, select beats based on the NPC's disposition and role.\n"
    "Do NOT use the old fields (in_combat, hp_changes, turn_order, drama_weight, advance_round).\n"
    "Those fields are removed. Use beat_selections only."
)

NARRATOR_CHASE_RULES: str = (
    "CHASE NARRATION RULES (active chase encounter):\n"
    "- 2-3 sentences. FAST. Breathless. Urgent.\n"
    "- Short sentences for sprinting. Fragments are fine.\n"
    '- "Left. The alley narrows. Something crashes behind you."\n'
    "- Each beat is a decision point — fork in the road, obstacle, closing gap.\n"
    '- End on the choice: "The fence or the fire escape?"\n'
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
    '- Show body language between lines: "She leans back, arms crossed."\n'
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
# Feature flag
# ---------------------------------------------------------------------------


def is_streaming_enabled() -> bool:
    """True when the narrator should use the streaming claude_client path.

    Gated by SIDEQUEST_NARRATOR_STREAMING env var. Default off to preserve
    existing synchronous behavior until the full streaming pipeline ships.
    """
    return os.environ.get("SIDEQUEST_NARRATOR_STREAMING", "0") == "1"


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
        encounter: StructuredEncounter | None = None,
        cdef: ConfrontationDef | None = None,
        encounter_summary: str | None = None,
        statuses_by_actor: dict[str, list] | None = None,
        resolution_signal: object | None = None,
        pc_classes_by_name: dict[str, tuple[ClassDef, float]] | None = None,
    ) -> None:
        """Inject encounter-specific narration rules + live encounter state.

        When ``encounter`` and ``cdef`` are given, render:
        1. The generic encounter-rules prose (unchanged — backwards compatible).
        2. The matched ConfrontationDef's beats + actors so the LLM emits
           valid ``beat_selections``.
        3. Both ascending dials (player_metric / opponent_metric).
        4. Per-actor statuses from ``statuses_by_actor``.
        5. Encounter tags.

        When ``resolution_signal`` is set, short-circuits to the one-shot
        [ENCOUNTER RESOLVED] zone and skips the live zone entirely.

        Port of NarratorAgent::build_encounter_context() in narrator.rs.
        Task 18: dual-dial encounter zone + resolution signal short-circuit.
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

        # One-shot resolution zone — wins over the active zone.
        if resolution_signal is not None:
            body = (
                "[ENCOUNTER RESOLVED]\n"
                f"type: {resolution_signal.encounter_type}\n"
                f"outcome: {resolution_signal.outcome}\n"
                f"final_player_metric: {resolution_signal.final_player_metric}\n"
                f"final_opponent_metric: {resolution_signal.final_opponent_metric}\n"
            )
            if resolution_signal.outcome == "yielded":
                yielded = ", ".join(resolution_signal.yielded_actors) or "(none)"
                body += (
                    f"yielded_actors: [{yielded}]\n"
                    f"edge_refreshed: {resolution_signal.edge_refreshed}\n"
                    "Describe the actor's exit on their own terms — they chose "
                    "to leave. Honor the choice. The opposing side does not "
                    "pursue or strike further.\n"
                )
            else:
                body += (
                    "The encounter has ended this turn. Describe the resolution "
                    "and any immediate transition out of the scene. Do NOT emit "
                    "beat_selections. Do NOT continue describing the encounter "
                    "as if it were active.\n"
                )
            registry.register_section(
                self.name(),
                PromptSection.new(
                    "narrator_encounter_resolved",
                    body,
                    AttentionZone.Early,
                    SectionCategory.State,
                ),
            )
            return  # short-circuit: do not render the live zone

        if encounter is not None and cdef is not None:
            statuses_by_actor = statuses_by_actor or {}
            actor_lines: list[str] = []
            for a in encounter.actors:
                statuses = statuses_by_actor.get(a.name, [])
                status_text = (
                    f"statuses: [{', '.join(f'{s.text} ({s.severity.value})' for s in statuses)}]"
                    if statuses
                    else "statuses: []"
                )
                actor_lines.append(f"  - {a.name} (side={a.side}, {status_text})")
            # All-beats listing — used by the narrator to pick OPPONENT beats
            # (the engine doesn't filter opponent options by class, since
            # NPCs/monsters resolve via opponent_default_stats / per-actor stats,
            # and any pack beat is fair game for the narrator to assign them).
            beat_lines = "\n".join(
                f"  - {b.id}: {b.label} (kind={b.kind.value}, base={b.base})" for b in cdef.beats
            )

            # Per-PC class-filtered beat menus (Task 7 of C&C B/X class beats).
            # When ``pc_classes_by_name`` is supplied, render a "X (Name) can:"
            # line for each PC actor showing only the beats their class can
            # legally pick this turn. Class filter chain lives in
            # ``sidequest.game.beat_filter.beats_available_for`` — single source
            # of truth so future B/X memorization (story #2) plugs in there,
            # not into prompt rendering. The all-beats block above stays for
            # opponent-side selection.
            pc_beat_lines = ""
            if pc_classes_by_name:
                from sidequest.game.beat_filter import beats_available_for
                from sidequest.telemetry.spans import confrontation_beat_filter_span

                pc_blocks: list[str] = []
                for actor in encounter.actors:
                    if actor.side != "player":
                        continue
                    entry = pc_classes_by_name.get(actor.name)
                    if entry is None:
                        continue
                    class_def, spell_slots = entry
                    available = beats_available_for(
                        cdef, class_def, spell_slots_remaining=spell_slots
                    )
                    available_ids = [b.id for b in available]
                    ids = ", ".join(available_ids) or "(none)"
                    pc_blocks.append(f"  - {class_def.display_name} ({actor.name}) can: {ids}")
                    # OTEL: GM-panel verifies the filter is wired, not just
                    # defined (CLAUDE.md OTEL-on-every-subsystem).
                    with confrontation_beat_filter_span(
                        actor=actor.name,
                        class_name=class_def.display_name,
                        confrontation_type=cdef.confrontation_type,
                        available_beat_ids=",".join(available_ids),
                        spell_slots_remaining=spell_slots,
                        pool_size=len(cdef.beats),
                        filtered_size=len(available),
                    ):
                        pass
                if pc_blocks:
                    pc_beat_lines = (
                        "Player-character beat menus — each PC's "
                        "beat_selection.beat_id MUST come from THEIR class's "
                        "list (cross-class picks are illegal):\n" + "\n".join(pc_blocks) + "\n"
                        "The player's available actions for this turn are listed above. "
                        "Do not narrate actions outside that list as performed.\n"
                    )
            tag_lines = (
                "\n".join(
                    f'  - "{t.text}" on {t.target or "(scene)"} '
                    f"({'fleeting' if t.fleeting else f'leverage {t.leverage}'}, "
                    f"created turn {t.created_turn})"
                    for t in encounter.tags
                )
                or "  (none)"
            )
            # Resolution-mode gate (combat fairness, 2026-04-26).
            # When the active confrontation is opposed_check, the engine
            # rolls dice for both sides and derives the outcome tier from
            # the shift between rolls. The narrator's job is to PICK
            # WHICH BEAT the opponent took (which action), but never the
            # outcome tier — that comes from the resolver. Without this
            # explicit gate the LLM tends to write "the orc swings and
            # connects, opening a gash on Sam's arm" (i.e. embeds an
            # outcome) which makes the engine-derived tier inconsistent
            # with the prose. See:
            # ``.archive/handoffs/opposed-checks-design.md``.
            from sidequest.genre.models.rules import ResolutionMode

            opposed_gate_text = ""
            if cdef.resolution_mode == ResolutionMode.opposed_check:
                opposed_gate_text = (
                    "RESOLUTION_MODE: opposed_check\n"
                    "When the active confrontation has resolution_mode: "
                    "opposed_check, you select only the OPPONENT'S BEAT "
                    "(which action). The engine rolls dice and derives "
                    "the outcome tier from the shift between your roll "
                    "and the player's. You DO NOT specify outcome tier "
                    "for either side — the dice decide. Describe the "
                    "opponent's action as it begins; do not narrate "
                    "whether it lands or fails until the engine returns "
                    "the resolved tier on the next turn.\n"
                )
            body = (
                f"<encounter-live>\n"
                f"Active encounter: {cdef.label} ({cdef.confrontation_type})\n"
                f"{opposed_gate_text}"
                f"Player metric: {encounter.player_metric.current} / "
                f"{encounter.player_metric.threshold}\n"
                f"Opponent metric: {encounter.opponent_metric.current} / "
                f"{encounter.opponent_metric.threshold}\n"
                f"Available beats — beat_selections.beat_id MUST be one of:\n"
                f"{beat_lines}\n"
                f"{pc_beat_lines}"
                f"Actors — emit a beat_selection for every non-withdrawn "
                f"non-neutral actor:\n" + "\n".join(actor_lines) + "\n"
                f"Encounter tags:\n{tag_lines}\n"
                f"</encounter-live>"
            )
            registry.register_section(
                self.name(),
                PromptSection.new(
                    "narrator_encounter_live",
                    body,
                    AttentionZone.Early,
                    SectionCategory.State,
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
