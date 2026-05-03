"""Orchestrator — Phase 1 narration turn pipeline.

Port of sidequest-agents/src/orchestrator.rs (Phase 1 slice only).
ADR-082: Python server narration vertical slice.

Phase 1 covers the narration-only turn path:
  Player action (raw text)
    → build context (world state + character + genre prompts)
    → call narrator agent via ClaudeClient
    → parse narrator response (narration text + game_patch JSON block)
    → return NarrationTurnResult

Phase boundaries are marked with:
  # Phase 1 slice: <subsystem> deferred to Story 41-<N>
and raise NotImplementedError when a deferred code path would be reached
(per CLAUDE.md "No Stubbing").

Out of scope (Phase 2+):
  - Combat encounter dispatch (Phase 3)
  - Dice request handling (Phase 2)
  - Scenario progression (Phase 5)
  - Advancement / beat firing (Phase 6)
  - Media/image/audio triggers (Phase 7)
  - Intent routing beyond state-override (Phase 1 uses exploration only, per ADR-067)
  - Continuity validation, lore filtering, world-builder injection (Phase 2+)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from sidequest.agents.claude_client import ClaudeClient, ClaudeResponse, LlmClient
from sidequest.agents.narrator import NarratorAgent, is_streaming_enabled
from sidequest.agents.prompt_framework.core import PromptRegistry
from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    SectionCategory,
)
from sidequest.game.chassis import ChassisInstance
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import GameSnapshot, Npc, NpcRegistryEntry, PartyPeer
from sidequest.game.tension_tracker import PacingHint
from sidequest.genre.models.lethality import LethalityPolicy
from sidequest.genre.models.narrative import Prompts
from sidequest.genre.models.pack import GenrePack
from sidequest.protocol.dice import RollOutcome
from sidequest.protocol.dispatch import DispatchPackage, NarratorDirective
from sidequest.telemetry.leak_audit import audit_canonical_prose
from sidequest.telemetry.phase_timing import PhaseTimings
from sidequest.telemetry.spans import (
    orchestrator_process_action_span,
    turn_agent_llm_inference_span,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt tier (ADR-066)
# ---------------------------------------------------------------------------

NARRATOR_MODEL: str = "opus"


class NarratorPromptTier:
    """Prompt tier selection (ADR-066).

    Full = first turn of a new session — everything included.
    Delta = subsequent turns on a resumed session — static context already
            in conversation history; only dynamic state + action sent.
    """

    Full = "full"
    Delta = "delta"


# ---------------------------------------------------------------------------
# Structured extraction types
# ---------------------------------------------------------------------------


@dataclass
class BeatSelection:
    """A single beat selection from the narrator's output (story 28-6).

    ``outcome`` is the resolved tier the prose describes. On free-text
    turns the narrator emits it; on dice-replay turns the engine
    overwrites it with the dice resolver's tier.

    Port of orchestrator.rs::BeatSelection.
    """

    actor: str
    beat_id: str
    outcome: RollOutcome = RollOutcome.Success  # default for legacy callers
    target: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BeatSelection:
        raw_outcome = d.get("outcome")
        if raw_outcome is None or raw_outcome == "":
            outcome = RollOutcome.Success
        else:
            try:
                outcome = RollOutcome(str(raw_outcome))
                # RollOutcome._missing_ returns Unknown instead of raising,
                # so check if we got Unknown from an invalid literal
                if outcome == RollOutcome.Unknown:
                    from sidequest.telemetry.spans import (
                        encounter_invalid_outcome_tier_span,
                    )

                    with encounter_invalid_outcome_tier_span(
                        beat_id=str(d.get("beat_id", "")),
                        actor=str(d.get("actor", "")),
                        declared_tier=str(raw_outcome),
                        valid_set="CritFail|Fail|Tie|Success|CritSuccess",
                    ):
                        pass
                    raise ValueError(
                        f"BeatSelection declared_tier={raw_outcome!r} not in RollOutcome"
                    )
            except ValueError as exc:
                # Re-raise our custom ValueError, not RollOutcome's
                if "declared_tier" in str(exc):
                    raise
                from sidequest.telemetry.spans import (
                    encounter_invalid_outcome_tier_span,
                )

                with encounter_invalid_outcome_tier_span(
                    beat_id=str(d.get("beat_id", "")),
                    actor=str(d.get("actor", "")),
                    declared_tier=str(raw_outcome),
                    valid_set="CritFail|Fail|Tie|Success|CritSuccess",
                ):
                    pass
                raise ValueError(
                    f"BeatSelection declared_tier={raw_outcome!r} not in RollOutcome"
                ) from exc
        return cls(
            actor=str(d.get("actor", "")),
            beat_id=str(d.get("beat_id", "")),
            outcome=outcome,
            target=d.get("target"),
        )


@dataclass
class VisualScene:
    """Visual scene description extracted from narrator JSON block.

    Port of orchestrator.rs::VisualScene.
    """

    subject: str
    tier: str = ""
    mood: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VisualScene:
        return cls(
            subject=str(d.get("subject", "")),
            tier=str(d.get("tier", "")),
            mood=str(d.get("mood", "")),
            tags=[str(t) for t in d.get("tags", [])],
        )


@dataclass
class NpcMention:
    """An NPC mentioned in the narrator's structured output.

    Accepts either a full struct or a bare string name.
    Fix: playtest-2026-04-12 — bare string NPC names caused serde rejection.

    Port of orchestrator.rs::NpcMention.
    """

    name: str
    pronouns: str = ""
    role: str = ""
    appearance: str = ""
    side: str = "neutral"
    is_new: bool = False

    @classmethod
    def from_value(cls, value: Any) -> NpcMention:
        valid_sides = {"player", "opponent", "neutral"}
        if isinstance(value, str):
            logger.debug("npc_mention.bare_string_fallback npc_name=%s", value)
            return cls(name=value, side="neutral")
        if isinstance(value, dict):
            side = str(value.get("side", "") or "neutral")
            if side not in valid_sides:
                from sidequest.telemetry.spans import encounter_invalid_side_span

                with encounter_invalid_side_span(
                    actor_name=str(value.get("name", "?")),
                    declared_side=side,
                    valid_set="player|opponent|neutral",
                ):
                    pass
                raise ValueError(f"NpcMention declared_side={side!r} not in {valid_sides}")
            return cls(
                name=str(value.get("name", "")),
                pronouns=str(value.get("pronouns", "")),
                role=str(value.get("role", "")),
                appearance=str(value.get("appearance", "")),
                side=side,
                is_new=bool(value.get("is_new", False)),
            )
        return cls(name=str(value), side="neutral")


@dataclass
class ActionRewrite:
    """Action rewrite from the narrator's game_patch JSON block.

    Port of orchestrator.rs::ActionRewrite.
    """

    you: str = ""
    named: str = ""
    intent: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionRewrite:
        return cls(
            you=str(d.get("you", "")),
            named=str(d.get("named", "")),
            intent=str(d.get("intent", "")),
        )


@dataclass
class NarrationTurnResult:
    """Result of processing a player action through the Phase 1 narration pipeline.

    Contains the prose narration and all structured fields extracted from the
    narrator's game_patch block. The orchestrator consumer (Story 41-6 server
    dispatch) reads these fields to emit protocol messages and apply state deltas.

    Phase 1 fields: narration, game_patch extraction, OTEL telemetry.
    Phase 2+ fields are absent — dispatch must not assume their presence.
    """

    # Core narration output
    narration: str
    is_degraded: bool = False

    # game_patch extracted fields (all optional — narrator may omit any)
    location: str | None = None
    scene_mood: str | None = None
    visual_scene: VisualScene | None = None
    confrontation: str | None = None
    beat_selections: list[BeatSelection] = field(default_factory=list)
    npcs_present: list[NpcMention] = field(default_factory=list)
    items_gained: list[dict[str, Any]] = field(default_factory=list)
    items_lost: list[dict[str, Any]] = field(default_factory=list)
    # Story 45-14: items dropped/abandoned in-world. Differs from items_lost
    # (gone from continuity — given away, destroyed, stolen) — discarded
    # items remain in inventory with state="Discarded" so they can be
    # narratively recovered. Plumbed through the same narration_apply seam.
    items_discarded: list[dict[str, Any]] = field(default_factory=list)
    # Story 45-15: items used up as consumables (patch-foam applied, ration
    # eaten, charge expended). Removed from inventory through the same
    # narration_apply seam as items_lost — distinguished as a separate lane
    # so the OTEL span can surface "consumable spent" vs. "given away" for
    # the GM panel lie-detector. Playtest 3 Felix found maintenance_kit
    # remained in inventory at quantity=1 after patch-foam use because no
    # extractor lane existed for the consume verb.
    items_consumed: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[dict[str, Any]] = field(default_factory=list)
    quest_updates: dict[str, str] = field(default_factory=dict)
    sfx_triggers: list[str] = field(default_factory=list)
    action_rewrite: ActionRewrite | None = None
    affinity_progress: list[tuple[str, int]] = field(default_factory=list)
    gold_change: int | None = None
    lore_established: list[str] | None = None
    status_changes: list[dict[str, Any]] = field(default_factory=list)
    # Magic system (Coyote Star iter 3 — Task 3.3). When the narrator
    # emits a ``magic_working`` field on its game_patch, this carries the
    # raw dict through to ``narration_apply.apply_magic_working`` for
    # validation + ledger application. ``None`` on every turn the
    # narrator does NOT invoke a magic working (the common case).
    magic_working: dict[str, Any] | None = None

    # Raw game_patch dict (plot-a-course Bundle 5). Carries the full parsed
    # game_patch JSON so narration_apply can dispatch sidecar intents (e.g.
    # plot_course / cancel_course) that aren't individually extracted fields.
    # Empty dict when the narrator emits no game_patch block or it fails to
    # parse (both treated as "no sidecar" by downstream handlers).
    game_patch_dict: dict[str, Any] = field(default_factory=dict)

    # OTEL / telemetry
    agent_name: str | None = None
    agent_duration_ms: int | None = None
    token_count_in: int | None = None
    token_count_out: int | None = None
    prompt_tier: str = NarratorPromptTier.Full
    prompt_text: str | None = None
    raw_response_text: str | None = None

    # Group G Task 5 — entries stripped from the DispatchPackage during
    # structural hiding. Items are ``SubsystemDispatch`` / ``NarratorDirective`` /
    # ``LethalityVerdict``; the session handler consumes these to emit
    # SECRET_NOTE events to their intended recipients (Task 6). Empty whenever
    # the decomposer did not run, or no entries were flagged with
    # ``redact_from_narrator_canonical``.
    secret_routes: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TurnContext — Phase 1 slice of orchestrator.rs::TurnContext
# ---------------------------------------------------------------------------


@dataclass
class TurnContext:
    """State flags and context passed into the narration turn pipeline.

    Phase 1 fields only. Phase 2+ fields (roll_outcome, tactical_grid_summary,
    world_graph, history_chapters, etc.) are not present — raise NotImplementedError
    if a caller attempts to use them before they are ported.

    Port of orchestrator.rs::TurnContext (Phase 1 slice).
    """

    # Encounter state (Phase 1: read to inject encounter rules)
    in_combat: bool = False
    in_chase: bool = False
    in_encounter: bool = False

    # Serialized game state summary for grounding narration (Valley zone)
    state_summary: str | None = None

    # Verbosity / vocabulary (Recency zone)
    narrator_verbosity: str = "standard"  # concise | standard | verbose
    narrator_vocabulary: str = "literary"  # accessible | literary | epic

    # Genre identity (Primacy zone — every tier)
    genre: str | None = None

    # Genre-specific prompt templates from prompts.yaml
    genre_prompts: Prompts | None = None

    # Player character name (Recency zone — action attribution)
    character_name: str = "Player"

    # Interaction count from `snapshot.turn_manager.interaction` at dispatch
    # time. Surfaced on the `prompt_assembled` watcher event so the
    # dashboard's Prompt tab can label the per-turn dropdown ("T3 · narrator
    # · 11k tokens"). Pre-fix the field was unset and the dropdown read
    # "T? · ? · 0 tokens" (playtest 2026-04-30 #1A).
    turn_number: int = 0

    # Multiplayer merged-turn payload. When the per-room barrier fires and
    # multiple PCs' actions are dispatched as a single narration turn, the
    # session handler stores `(character_name, action_text)` per submitter
    # here so build_narrator_prompt can render a multi-PC declaration block
    # instead of a single `"<one PC> says: <merged blob>"` line — that
    # framing both attributed every PC's words to the dispatch winner and
    # invited the LLM to generate dialogue for PCs whose players had only
    # declared physical actions (SOUL.md "Agency" violation flagged in the
    # 2026-04-29 multiplayer playtest). When `None`, the prompt falls back
    # to the single-player format.
    merged_player_actions: list[tuple[str, str]] | None = None

    # Current location (for degraded response)
    current_location: str = "Unknown"

    # SFX library (Valley zone, Full tier only)
    available_sfx: list[str] = field(default_factory=list)

    # Trope beat directives from previous turn (Early zone)
    pending_trope_context: str | None = None

    # Opening-turn narrator directive (Early zone, turn 0 only).
    # Story 2.3 Slice H / ADR-082: the session handler resolves an
    # opening hook at connect time and stashes the rendered directive
    # here for the first narration turn. Subsequent turns re-build the
    # TurnContext without it (the handler zeroes opening_directive
    # after consumption), matching Rust's `opening_directive.take()`.
    opening_directive: str | None = None

    # Persistent narrator world context (Valley zone, every turn).
    # Story 41-11 / ADR-082 Phase 2.2 IOU: resolved once at connect time
    # in the session handler. Currently contains the ``AVAILABLE
    # CULTURES`` block produced by
    # :func:`sidequest.server.dispatch.culture_context.resolve_culture_reference`
    # (with lore-only cultures filtered out via ``Culture.chargen``).
    # Phase 3 work will prepend the setting + world-lore blocks here.
    # Rust parity: ``world_context`` string threaded through
    # ``connect.rs`` and consumed by ``WorldBuilder::inject_world_context``.
    world_context: str | None = None

    # Active trope summary for background context (Valley zone)
    active_trope_summary: str | None = None

    # NPC registry entries (for merchant context injection — Phase 1 slice: skipped)
    npc_registry: list[NpcRegistryEntry] = field(default_factory=list)

    # Full NPC structs (for merchant context injection — Phase 1 slice: skipped)
    npcs: list[Npc] = field(default_factory=list)

    # Chassis registry — chassis-as-speaker voice data (register, vocal tics,
    # bond-tier address-form). Defensive copy from session.chassis_registry
    # since TurnContext is a snapshot. Empty for non-rig genres.
    chassis_registry: dict[str, ChassisInstance] = field(default_factory=dict)

    # Party peer identity packets (Story 37-36). Canonical name/pronouns/
    # race/class/level for every non-self PC in the session. Empty on
    # solo sessions — in that case the injector registers no section so
    # we keep the zero-byte-leak discipline (see NPC roster for parallel).
    party_peers: list[PartyPeer] = field(default_factory=list)

    # Chassis-interior positions for every PC in the session
    # (``character_name -> current_room``). Renders into the narrator
    # prompt as the "CREW POSITIONS" section so the narrator knows where
    # each PC is on the Kestrel and can state-patch movements. Empty dict
    # (no chassis aboard) registers no section — zero-byte-leak.
    pc_positions: dict[str, str | None] = field(default_factory=dict)

    # PacingHint from TensionTracker (Late zone — Rust parity at
    # sidequest-api/crates/sidequest-agents/src/prompt_framework/mod.rs:108).
    # Story 42-3 / ADR-082 Phase 3. When ``None``, no pacing section is
    # registered into the narrator prompt — zero byte leak.
    #
    # Spec deviation logged in 42-3 session: context-doc says ``str | None``,
    # but a string field would discard ``escalation_beat`` and force the
    # caller to pre-render the directive. Storing the typed object lets the
    # call site marshal exactly what the Python ``register_pacing_section``
    # helper requires — ``(narrator_directive: str, escalation_beat: str | None)``.
    # Note: Rust's helper takes ``&PacingHint`` directly and does the
    # marshalling internally; Python's helper takes two derived strings, so
    # the call site at ``build_narrator_prompt`` does the marshalling. The
    # *field* mirrors Rust's typed seam; the *helper signatures* differ.
    pacing_hint: PacingHint | None = None

    # Encounter state summary rendered for the Valley zone (Story 3.4).
    # When ``None``, no encounter section is registered. Mutually consistent
    # with ``in_combat``/``in_chase``/``in_encounter`` — if any of those is
    # True, ``encounter_summary`` should be set.
    encounter_summary: str | None = None

    # The matched ConfrontationDef for the active encounter (Story 3.4).
    # Typed as ``Any`` to avoid a circular import through sidequest.genre;
    # runtime shape is ``sidequest.genre.models.rules.ConfrontationDef``.
    # The narrator uses this to render available beats + actors into the
    # Early zone so the LLM can emit valid ``beat_selections``.
    confrontation_def: Any = None

    # Genre pack's full menu of confrontation types — list of
    # ``(type, label, category)`` triples drawn from
    # ``pack.rules.confrontations``. Rendered into the narrator prompt
    # (when no encounter is active) so the LLM picks the most specific
    # type rather than defaulting to generic ``combat``. Playtest
    # 2026-04-25 regression: in space_opera, the narrator picked
    # ``combat`` (Firefight) for a starship dogfight even though the
    # genre's ``rules.yaml`` declares ``ship_combat`` (vessel scale)
    # and ``dogfight`` side-by-side. The menu was implicit; the LLM
    # couldn't see what was on offer.
    available_confrontations: list[tuple[str, str, str]] = field(default_factory=list)

    # Live encounter object (Story 3.4). Typed as ``Any`` to avoid a
    # circular import through sidequest.game. Runtime type:
    # ``sidequest.game.encounter.StructuredEncounter``.
    encounter: Any = None

    # Retrieved lore fragments for the current turn (Valley zone, Story
    # 37-33). Pre-rendered by the session handler via
    # :func:`sidequest.game.lore_embedding.retrieve_lore_context` before
    # the turn fires. ``None`` means no lore section is registered —
    # keeps the prompt zone-clean when the daemon is unavailable or the
    # store is empty. The retrieval helper never returns an empty string
    # (all non-producing paths return ``None``; the producing path
    # returns a non-empty ``<lore>`` block).
    lore_context: str | None = None

    # Group B (Local DM decomposer) — session handler populates before calling
    # run_narration_turn. Consumed by build_narrator_prompt to register the
    # narrator_directives PromptSection. Default None = decomposer did not run.
    dispatch_package: DispatchPackage | None = None

    # Group C — LethalityArbiter inputs. Session handler populates all three
    # from the active GenrePack + live snapshot before run_narration_turn.
    # When ``lethality_policy`` is non-None, build_narrator_prompt runs the
    # arbiter after run_dispatch_bank and merges its paired must/must-not
    # directives into the same narrator_directives PromptSection.
    lethality_policy: LethalityPolicy | None = None
    pc_cores_by_player: dict[str, CreatureCore] = field(default_factory=dict)
    npc_cores_by_name: dict[str, CreatureCore] = field(default_factory=dict)

    # Per-actor status lists (Task 18 — dual-track momentum). Populated by
    # run_narration_turn from session.characters so the live encounter zone
    # renders Status objects per actor. Typed as dict[str, list[Any]] to avoid
    # a circular import on Status — matches the existing pattern for
    # ``confrontation_def: Any`` and ``encounter: Any``.
    statuses_by_actor: dict[str, list[Any]] = field(default_factory=dict)

    # One-shot ResolutionSignal (Task 18 — dual-track momentum). Populated by
    # run_narration_turn from session.pending_resolution_signal. Consumed in
    # build_narrator_prompt: passed to build_encounter_context, span fired.
    # Cleared on the snapshot in the module-level wrapper after the orchestrator
    # returns — TurnContext is a local copy; the snapshot isn't in scope here.
    # Typed as Any to avoid a circular import on ResolutionSignal.
    pending_resolution_signal: Any = None

    # Magic state for the current world (Valley zone).
    # When non-None, build_narrator_prompt injects the magic-context block so
    # the narrator knows the active plugins, hard_limits, and per-actor ledger
    # bars before composing narration for any magic working.
    magic_state: Any = None  # runtime type: sidequest.magic.state.MagicState | None

    # Per-turn phase-timing accumulator (Story: phase-timing instrumentation).
    # Defaults to PhaseTimings.NULL so legacy fixtures and partial mocks
    # continue to work without provisioning a real timer. Real instances
    # are populated by ``_execute_narration_turn`` at action receipt.
    phase_timings: PhaseTimings = field(default_factory=lambda: PhaseTimings.NULL)

    # Orbital tier fields (plot-a-course). Populated by _build_turn_context
    # when the world has an orbital tier (orbital_content is not None).
    # When None/empty, build_narrator_prompt skips the <courses> block —
    # zero byte leak on non-orbital worlds.
    #
    # Types are Any to avoid a circular import on OrbitalContent/Scope;
    # runtime types are:
    #   orbital_content: sidequest.orbital.loader.OrbitalContent | None
    #   orbital_scope:   sidequest.orbital.render.Scope | None
    #   party_body_id:   str | None  (from snapshot.party_body_id)
    #   recent_body_mentions: collections.deque[str]  (from Session)
    #   quest_anchors:   list[str]  (from snapshot.quest_anchors)
    orbital_content: Any = None
    orbital_scope: Any = None
    party_body_id: str | None = None
    recent_body_mentions: Any = field(default_factory=list)  # deque[str] or list[str]
    quest_anchors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# game_patch extraction helpers
# ---------------------------------------------------------------------------


def _extract_game_patch_json(raw: str) -> dict[str, Any]:
    """Extract and parse the ```game_patch``` block from a raw narrator response.

    Tries ```game_patch first, then falls back to ```json, then returns {}.
    Parse failures are non-fatal: warns and returns empty dict.

    Port of extract_game_patch() in orchestrator.rs.
    """
    # Primary: ```game_patch ... ```
    idx = raw.find("```game_patch")
    if idx != -1:
        after_label = idx + len("```game_patch")
        end_idx = raw.find("```", after_label)
        if end_idx != -1:
            json_str = raw[after_label:end_idx].strip()
            try:
                result = json.loads(json_str)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError as e:
                logger.warning("game_patch block found but failed to parse: %s", e)

    # Fallback: ```json ... ```
    idx = raw.find("```json")
    if idx != -1:
        after_label = idx + len("```json")
        end_idx = raw.find("```", after_label)
        if end_idx != -1:
            json_str = raw[after_label:end_idx].strip()
            try:
                result = json.loads(json_str)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

    return {}


def _strip_json_fence(text: str) -> str:
    """Remove fenced code blocks from narration so the player sees clean prose.

    Takes prose BEFORE the fence block; discards the block and everything after.
    Per narrator contract: prose-then-patch, nothing after.

    Port of strip_json_fence() in orchestrator.rs.
    """
    pattern = re.compile(r"(?s)```(?:json|game_patch)?\s*\n[\s\S]*?\n```")
    match = pattern.search(text)
    if match:
        prose_before = text[: match.start()]
        after_block = text[match.end() :]
        if after_block.strip():
            logger.warning(
                "strip_json_fence: discarding post-patch content "
                "(likely meta-commentary) after_len=%d preview=%r",
                len(after_block.strip()),
                after_block.strip()[:80],
            )
        return prose_before.strip()
    return text.strip()


def extract_structured_from_response(raw: str) -> dict[str, Any]:
    """Extract the narrator's prose and all structured fields from a raw response.

    The narrator emits a ```game_patch { ... }``` block every turn containing
    footnotes, items, NPCs, mood, etc. This function parses that block and
    maps it to a plain dict, then strips the fence from the returned prose.

    Returns a dict with keys:
      prose, footnotes, items_gained, items_lost, npcs_present, quest_updates,
      visual_scene, scene_mood, sfx_triggers, action_rewrite,
      beat_selections, confrontation, location, affinity_progress, gold_change,
      lore_established.

    Port of extract_structured_from_response() in orchestrator.rs.
    """
    # Parse game_patch before stripping
    patch = _extract_game_patch_json(raw)

    # Log extraction counts for OTEL visibility
    logger.info(
        "game_patch.extracted "
        "footnotes=%d items_gained=%d items_lost=%d items_discarded=%d "
        "items_consumed=%d "
        "npcs_present=%d quest_updates=%d sfx_triggers=%d "
        "has_visual_scene=%s has_scene_mood=%s has_action_rewrite=%s "
        "beat_selections=%d confrontation=%r "
        "has_location=%s gold_change=%r status_changes=%d",
        len(patch.get("footnotes", [])),
        len(patch.get("items_gained", [])),
        len(patch.get("items_lost", [])),
        len(patch.get("items_discarded", [])),
        len(patch.get("items_consumed", [])),
        len(patch.get("npcs_present", patch.get("npcs_met", []))),
        len(patch.get("quest_updates", {})),
        len(patch.get("sfx_triggers", [])),
        patch.get("visual_scene") is not None,
        patch.get("mood") is not None or patch.get("scene_mood") is not None,
        patch.get("action_rewrite") is not None,
        len(patch.get("beat_selections", [])),
        patch.get("confrontation"),
        patch.get("location") is not None,
        patch.get("gold_change"),
        len(patch.get("status_changes", [])),
    )

    prose = _strip_json_fence(raw)

    return {
        "prose": prose,
        "footnotes": patch.get("footnotes", []),
        "items_gained": patch.get("items_gained", []),
        "items_lost": patch.get("items_lost", []),
        "items_discarded": patch.get("items_discarded", []),
        "items_consumed": patch.get("items_consumed", []),
        "npcs_present": patch.get("npcs_present", patch.get("npcs_met", [])),
        "quest_updates": patch.get("quest_updates", {}),
        "visual_scene": patch.get("visual_scene"),
        "scene_mood": patch.get("scene_mood", patch.get("mood")),
        "sfx_triggers": patch.get("sfx_triggers", []),
        "action_rewrite": patch.get("action_rewrite"),
        "beat_selections": patch.get("beat_selections", []),
        "confrontation": patch.get("confrontation"),
        "location": patch.get("location"),
        "affinity_progress": [
            (str(d["name"]), int(d.get("delta", 1)))
            for d in patch.get("affinity_progress", [])
            if isinstance(d, dict) and "name" in d
        ],
        "gold_change": patch.get("gold_change"),
        "lore_established": patch.get("lore_established"),
        "status_changes": patch.get("status_changes", []),
        # Magic system (Coyote Star iter 3 — Task 3.3). Forwarded as a
        # raw dict; pydantic validation happens in
        # ``narration_apply.apply_magic_working`` so the parse error is
        # raised at the apply seam (where ``MagicWorkingParseError`` is
        # defined) rather than during extraction.
        "magic_working": patch.get("magic_working"),
    }


# ---------------------------------------------------------------------------
# Prompt assembly helpers (ContextBuilder equivalent — inlined per spec)
# ---------------------------------------------------------------------------


def _build_verbosity_section(verbosity: str) -> str:
    """Build the narrator verbosity constraint text for the given setting.

    Port of the verbosity match block in build_narrator_prompt_tiered().
    """
    if verbosity == "concise":
        return (
            "<critical>\n"
            "<length-limit>\n"
            "HARD LIMIT: Maximum 4 sentences of prose. DO NOT EXCEED 400 characters of narrative text.\n"
            "This overrides all other length guidance. If a trope beat or genre instruction "
            "would push you past this limit, cut description — never cut the limit.\n"
            "Action and consequence only. No atmosphere. No sensory detail.\n"
            "The game_patch JSON does not count toward this limit.\n"
            "</length-limit>\n"
            "</critical>"
        )
    if verbosity == "verbose":
        return (
            "<critical>\n"
            "<length-limit>\n"
            "HARD LIMIT: Maximum 10 sentences of prose. DO NOT EXCEED 1000 characters of narrative text.\n"
            "This overrides all other length guidance. If a trope beat or genre instruction "
            "would push you past this limit, cut description — never cut the limit.\n"
            "Rich atmosphere for arrivals and reveals. Shorter for simple actions.\n"
            "The game_patch JSON does not count toward this limit.\n"
            "</length-limit>\n"
            "</critical>"
        )
    # Default: standard (also handles unknown values)
    return (
        "<critical>\n"
        "<length-limit>\n"
        "HARD LIMIT, per acting PC this turn: maximum 8 sentences and 800 characters of prose.\n"
        "If no PCs are acting (scene anchor, transition, or pure narrator beat), the same\n"
        "limit applies to the whole response: 8 sentences / 800 characters total.\n"
        "This overrides all other length guidance. If a trope beat, genre voice instruction, "
        "or MUST-weave directive would push you past this limit, cut description — never cut the limit.\n"
        "Each acting PC gets one short paragraph for simple actions, "
        "or two short paragraphs for arrivals or reveals. Give every PC their own beat — "
        "do not collapse two PCs' actions into a single sentence to save room.\n"
        "The game_patch JSON block does not count toward this limit.\n"
        "Count sentences per PC before responding. If any PC has more than 8, cut that PC's beat.\n"
        "</length-limit>\n"
        "</critical>"
    )


def _build_vocabulary_section(vocabulary: str) -> str:
    """Build the narrator vocabulary instruction text for the given setting.

    Port of the vocabulary match block in build_narrator_prompt_tiered().
    """
    if vocabulary == "accessible":
        return (
            "[NARRATION VOCABULARY]\n"
            "Use simple, direct language. Prefer common words over obscure "
            "ones. Keep sentences short and clear. Aim for approximately "
            "8th-grade reading level. No archaic constructions or elaborate "
            "metaphors."
        )
    if vocabulary == "epic":
        return (
            "[NARRATION VOCABULARY]\n"
            "Use elevated, archaic, or mythic diction. Embrace elaborate "
            "sentence structures, rare words, and poetic constructions. "
            "Channel the cadence of sagas, epics, and high fantasy prose. "
            "Unrestricted complexity."
        )
    # Default: literary (also handles unknown values)
    return (
        "[NARRATION VOCABULARY]\n"
        "Use rich but clear prose. Employ varied vocabulary and literary "
        "devices where they serve the narrative. Balance elegance with "
        "accessibility — vivid but not purple."
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Phase 1 narration orchestrator.

    Routes player input → context assembly → narrator agent → game_patch extraction.
    This is the Python port of the Phase 1 path through orchestrator.rs::Orchestrator.

    The narrator is the unified agent (ADR-067). All intents route to narrator.
    Session management follows ADR-066 (persistent Opus sessions via --resume).

    Phase 2+ subsystems (combat dispatch, dice, world-builder injection, lore
    filtering, merchant context) are deferred. See phase-marker comments below.
    """

    def __init__(
        self,
        client: LlmClient | None = None,
        soul_data: object | None = None,
    ) -> None:
        """Create an orchestrator.

        Args:
            client: LlmClient client for LLM invocations.
                    If None, creates a default ClaudeClient.
            soul_data: Optional SoulData for SOUL.md principle injection.
                       If None, SOUL.md is loaded from CWD (if present).
        """
        self._client: LlmClient = client if client is not None else ClaudeClient()
        self._narrator = NarratorAgent()

        # Persistent session management (ADR-066)
        self._narrator_session_id: str | None = None
        self._session_genre: str | None = None
        self._session_lock: Lock = Lock()

        # SOUL.md principles (optional)
        if soul_data is not None:
            self._soul_data = soul_data
        else:
            # Attempt to load SOUL.md from CWD
            import pathlib

            from sidequest.agents.prompt_framework.soul import parse_soul_md

            soul_path = pathlib.Path("SOUL.md")
            loaded = parse_soul_md(soul_path)
            self._soul_data = loaded if loaded else None

        # Group G Task 5 — secret routes captured during the most recent
        # ``build_narrator_prompt`` call. Populated by ``redact_dispatch_package``
        # when the incoming DispatchPackage contains entries flagged
        # ``redact_from_narrator_canonical``. Read by ``run_narration_turn`` to
        # attach onto the NarrationTurnResult so the session handler can route
        # them as SECRET_NOTE events (Task 6).
        self._last_secret_routes: list[object] = []

    # ------------------------------------------------------------------
    # Session lifecycle (ADR-066)
    # ------------------------------------------------------------------

    def reset_narrator_session(self) -> None:
        """Reset the narrator session, forcing next prompt to use Full tier.

        Call when switching games, loading a different save, or after genre switch.
        Port of orchestrator.rs::Orchestrator::reset_narrator_session.
        """
        with self._session_lock:
            logger.info("orchestrator.narrator_session_reset reason=session_lifecycle")
            self._narrator_session_id = None
            self._session_genre = None

    def set_narrator_session_id(self, session_id: str) -> None:
        """Set the narrator session ID (for testing and server dispatch)."""
        with self._session_lock:
            self._narrator_session_id = session_id

    # ------------------------------------------------------------------
    # Group G Task 7 — entity token resolver for the leak audit
    # ------------------------------------------------------------------

    def _entity_tokens_for_registry(
        self,
        context: TurnContext,
    ) -> dict[str, list[str]]:
        """Build ``entity_id -> [tokens]`` from the session's NPC registry.

        In the current data model the ``target`` field in a SubsystemDispatch
        is the NPC name (there is no separate entity_id on
        :class:`NpcRegistryEntry` yet). We therefore key the token map by
        ``entry.name`` and populate with ``[name, role]`` where ``role`` is
        a non-empty role noun. No alias field exists today — a partial
        token set is still a working audit.
        """
        tokens: dict[str, list[str]] = {}
        for entry in context.npc_registry:
            toks: list[str] = []
            if entry.name:
                toks.append(entry.name)
            if entry.role:
                toks.append(entry.role)
            if toks:
                tokens[entry.name] = toks
        for npc in context.npcs:
            name = npc.core.name if npc.core else None
            if not name or name in tokens:
                continue
            toks = [name]
            tokens[name] = toks
        return tokens

    def has_active_narrator_session(self) -> bool:
        """Check whether a narrator session is currently active."""
        with self._session_lock:
            return self._narrator_session_id is not None

    def select_prompt_tier(self, context: TurnContext) -> str:
        """Select the prompt tier based on session state and genre match.

        Returns Full if no session exists or if the genre has changed.
        Port of orchestrator.rs::Orchestrator::select_prompt_tier.
        """
        with self._session_lock:
            current_session = self._narrator_session_id is not None
            if not current_session:
                return NarratorPromptTier.Full

            # Genre switch detection
            if (
                context.genre is not None
                and self._session_genre is not None
                and context.genre != self._session_genre
            ):
                logger.warning(
                    "Genre switch detected — clearing stale session and forcing Full tier "
                    "incoming_genre=%s",
                    context.genre,
                )
                self._narrator_session_id = None
                self._session_genre = None
                return NarratorPromptTier.Full

        return NarratorPromptTier.Delta

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    async def build_narrator_prompt(
        self,
        action: str,
        context: TurnContext,
        tier: str = NarratorPromptTier.Full,
    ) -> tuple[str, PromptRegistry]:
        """Build the narrator prompt for a turn (without invoking the LLM).

        Returns (prompt_text, registry) so callers can inspect zone breakdown.
        This is the Phase 1 port of build_narrator_prompt_tiered() in orchestrator.rs.

        Phase 1 omissions (all deferred):
          - LoreFilter world-graph injection (Phase 2 — story 23-4)
          - WorldBuilderAgent history chapter injection (Phase 3 — story 15-18)
          - Merchant context injection (Phase 2 — story 15-16)
          - Tactical grid summary (Phase 3 — story 29-11)
          - Script tool injection (Phase 7 — ADR-056)
          - RollOutcome injection (Phase 2 — story 34-9)
          - Backstory capture directive (Phase 1 only for Backstory intent, which
            is not yet classified in Phase 1)
        """
        registry = PromptRegistry()
        agent_name = self._narrator.name()
        is_full = tier == NarratorPromptTier.Full

        # Group G Task 5 — Structural hiding. Strip every DispatchPackage
        # entry flagged ``redact_from_narrator_canonical`` BEFORE anything
        # downstream reads it. The narrator prompt never sees a redacted
        # entry; ``removed`` is stashed on the orchestrator so
        # ``run_narration_turn`` can forward it to the session handler for
        # SECRET_NOTE routing (Task 6).
        visible_dispatch_package = context.dispatch_package
        if context.dispatch_package is not None:
            from sidequest.agents.prompt_redaction import redact_dispatch_package

            visible_dispatch_package, removed = redact_dispatch_package(context.dispatch_package)
            self._last_secret_routes = list(removed)
        else:
            self._last_secret_routes = []

        # === STATIC SECTIONS (Full tier only — already in session history on Delta) ===

        if is_full:
            # ADR-067: Always narrator identity (unified agent)
            self._narrator.build_context(registry)

            # Always inject dialogue rules — short and NPCs can appear anytime
            self._narrator.build_dialogue_context(registry)

            # SOUL principles (Early zone)
            if self._soul_data is not None:
                from sidequest.agents.prompt_framework.soul import SoulData

                if isinstance(self._soul_data, SoulData):
                    filtered = self._soul_data.as_prompt_text_for(agent_name)
                    if filtered:
                        registry.register_section(
                            agent_name,
                            PromptSection.new(
                                "soul_principles",
                                filtered,
                                AttentionZone.Early,
                                SectionCategory.Soul,
                            ),
                        )

        # === OUTPUT FORMAT (every tier — narrator must always know game_patch schema) ===
        self._narrator.build_output_format(registry)

        # === GENRE IDENTITY (every tier — narrator MUST always know the genre) ===
        # Fix: playtest-2026-04-05 — narrator broke fourth wall asking "What genre is Ashgate Square in?"
        if context.genre:
            genre_display = context.genre.replace("_", " ")
            logger.info(
                "orchestrator.genre_identity_injection genre=%s tier=%s",
                context.genre,
                tier,
            )
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "genre_identity",
                    (
                        f"<genre>\nYou are narrating a {genre_display} game. This is the genre — "
                        "use its tone, vocabulary, tropes, and conventions in every response. "
                        "Never ask the player what genre, setting, or system they are playing. "
                        "You already know.\n</genre>"
                    ),
                    AttentionZone.Primacy,
                    SectionCategory.Identity,
                ),
            )

        # === GENRE PROMPT TEMPLATES (from prompts.yaml) ===
        if context.genre_prompts is not None:
            gp = context.genre_prompts

            # Narrator voice — every tier (story 30-2)
            if gp.narrator:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_narrator_voice",
                        f"<genre-voice>\n{gp.narrator}\n</genre-voice>",
                        AttentionZone.Primacy,
                        SectionCategory.Identity,
                    ),
                )

            # NPC behavior — every tier (story 30-2)
            if gp.npc:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_npc_voice",
                        f"<genre-npc>\n{gp.npc}\n</genre-npc>",
                        AttentionZone.Early,
                        SectionCategory.Genre,
                    ),
                )

            # World state tracking — every tier (story 30-2)
            if gp.world_state:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_world_state",
                        f"<genre-world-state>\n{gp.world_state}\n</genre-world-state>",
                        AttentionZone.Early,
                        SectionCategory.Genre,
                    ),
                )

            # Combat — every tier (combat can start mid-session)
            if context.in_combat and gp.combat:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_combat_voice",
                        f"<genre-combat>\n{gp.combat}\n</genre-combat>",
                        AttentionZone.Early,
                        SectionCategory.Genre,
                    ),
                )

            # Chase — every tier (chase can start mid-session)
            if context.in_chase and gp.chase:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_chase_voice",
                        f"<genre-chase>\n{gp.chase}\n</genre-chase>",
                        AttentionZone.Early,
                        SectionCategory.Genre,
                    ),
                )

            # Extraction — every tier
            if gp.extraction:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "genre_extraction",
                        f"<genre-extraction>\n{gp.extraction}\n</genre-extraction>",
                        AttentionZone.Valley,
                        SectionCategory.Genre,
                    ),
                )

            # Full-tier-only genre sections
            if is_full:
                if gp.keeper_monologue:
                    registry.register_section(
                        agent_name,
                        PromptSection.new(
                            "genre_keeper_monologue",
                            f"<genre-keeper>\n{gp.keeper_monologue}\n</genre-keeper>",
                            AttentionZone.Valley,
                            SectionCategory.Genre,
                        ),
                    )

                if gp.town:
                    registry.register_section(
                        agent_name,
                        PromptSection.new(
                            "genre_town",
                            f"<genre-town>\n{gp.town}\n</genre-town>",
                            AttentionZone.Valley,
                            SectionCategory.Genre,
                        ),
                    )

                if gp.chargen:
                    registry.register_section(
                        agent_name,
                        PromptSection.new(
                            "genre_chargen",
                            f"<genre-chargen>\n{gp.chargen}\n</genre-chargen>",
                            AttentionZone.Valley,
                            SectionCategory.Genre,
                        ),
                    )

                if gp.transition_hints:
                    hints = [f'  {k}: "{v}"' for k, v in gp.transition_hints.items()]
                    registry.register_section(
                        agent_name,
                        PromptSection.new(
                            "genre_transition_hints",
                            "transition_hints:\n" + "\n".join(hints),
                            AttentionZone.Late,
                            SectionCategory.Format,
                        ),
                    )

        # === STATE-DEPENDENT SECTIONS (every tier) ===

        # Available confrontation menu — render when no encounter is
        # active, so the narrator's ``confrontation`` field maps to the
        # most specific type the genre offers (e.g., ``ship_combat`` /
        # ``dogfight`` instead of generic ``combat``). The narrator
        # prompt at ``narrator.py:135-148`` already references
        # "AVAILABLE ENCOUNTER TYPES in game_state" — this section is
        # what fulfills that contract. Suppressed when an encounter is
        # already live (the encounter-live zone enumerates the active
        # type's beats + actors; alternates aren't relevant per the
        # narrator rule "Only include on the turn the encounter STARTS").
        # Playtest 2026-04-25 regression: in space_opera the narrator
        # picked ``combat`` (Firefight) for a starship dogfight even
        # though the genre's rules.yaml declares ship_combat (vessel
        # scale) and dogfight side-by-side.
        if (
            context.available_confrontations
            and not context.in_combat
            and not context.in_chase
            and not context.in_encounter
            and context.pending_resolution_signal is None
        ):
            menu_lines = "\n".join(
                f"- {cdef_type}: {cdef_label}" + (f" (category={cdef_cat})" if cdef_cat else "")
                for cdef_type, cdef_label, cdef_cat in context.available_confrontations
            )
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "narrator_available_confrontations",
                    (
                        "<available-encounter-types>\n"
                        "AVAILABLE ENCOUNTER TYPES (for the ``confrontation`` "
                        "field — pick the MOST SPECIFIC type that matches the "
                        "fiction; never default to a generic ``combat`` if a "
                        "more specific type is on the list):\n"
                        f"{menu_lines}\n"
                        "</available-encounter-types>"
                    ),
                    AttentionZone.Early,
                    SectionCategory.State,
                ),
            )

        # Encounter rules for ANY active encounter type. The narrator's
        # build_encounter_context call renders live beats + actors + both dials
        # + per-actor statuses + tags directly into the registry.
        # Also fires when only pending_resolution_signal is set — the encounter
        # flags may have been cleared by the engine on the resolution turn, but
        # the [ENCOUNTER RESOLVED] zone must still be emitted this turn.
        if (
            context.in_combat
            or context.in_chase
            or context.in_encounter
            or context.pending_resolution_signal is not None
        ):
            self._narrator.build_encounter_context(
                registry,
                encounter=context.encounter,
                cdef=context.confrontation_def,
                encounter_summary=context.encounter_summary,
                statuses_by_actor=context.statuses_by_actor,
                resolution_signal=context.pending_resolution_signal,
            )
            if context.pending_resolution_signal is not None:
                from sidequest.telemetry.spans import (
                    encounter_resolution_signal_consumed_span,
                )
                from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

                sig = context.pending_resolution_signal
                with encounter_resolution_signal_consumed_span(
                    outcome=sig.outcome,
                    final_player_metric=sig.final_player_metric,
                    final_opponent_metric=sig.final_opponent_metric,
                ):
                    pass
                _watcher_publish(
                    "state_transition",
                    {
                        "field": "encounter",
                        "op": "resolution_signal_consumed",
                        "outcome": sig.outcome,
                        "final_player_metric": sig.final_player_metric,
                        "final_opponent_metric": sig.final_opponent_metric,
                    },
                    component="encounter",
                )

        # Phase 1 slice: tactical grid injection deferred to Story 41-9
        # Phase 1 slice: lore filter (world_graph) deferred to Story 41-7
        # Phase 1 slice: world-builder history chapter injection deferred to Story 41-8
        # Phase 1 slice: merchant context injection deferred to Story 41-7

        # Opening-turn directive (Early zone, turn 0 only).
        # Story 2.3 Slice H: the session handler feeds the resolved
        # opening hook's prompt into the narrator's Early zone for
        # the opening turn, then clears the field so later turns run
        # directive-free. Placed ahead of trope directives because the
        # opening always wins when both are set — there shouldn't be
        # trope carryover on turn 0 anyway.
        if context.opening_directive:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "opening_directive",
                    context.opening_directive,
                    AttentionZone.Early,
                    SectionCategory.State,
                ),
            )

        # Trope beat directives (Early zone)
        if context.pending_trope_context:
            logger.info("orchestrator.trope_beat_injection beats_injected=1")
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "trope_beat_directives",
                    context.pending_trope_context,
                    AttentionZone.Early,
                    SectionCategory.State,
                ),
            )

        # NPC roster — canonical identity anchor (Early zone). Story 37-44.
        # Without this the narrator cannot see the registry and reinvents
        # pronouns/role each turn (playtest 3: Frandrew she/her captain →
        # he/him grease monkey in 10 turns).
        if context.npc_registry:
            registry.register_npc_roster_section(agent_name, context.npc_registry)

        # Chassis voices — chassis as named speakers with bond-tier name-form.
        # See register_chassis_voice_section docstring; mirrors npc_roster
        # discipline. Slice scope: addresses-form derived from the active
        # character's name; bond_seed placeholder id "player_character"
        # rebinds to real player_id at chargen wiring (follow-up).
        if context.chassis_registry:
            registry.register_chassis_voice_section(
                agent_name,
                context.chassis_registry,
                context.character_name,
            )

        # Party-peer roster — canonical identity anchor for other PCs.
        # Story 37-36 (port-drift reopen). In sealed-letter multiplayer,
        # Player A's narrator turn needs ground truth about Players B/C/...
        # or their pronouns/race/class drift save-to-save (playtest 3:
        # Blutka he/him in own save became she/her in Orin's save).
        if context.party_peers:
            # ``peer_count`` is the count of OTHER PCs (party_peers excludes
            # self). Pre-fix the field was named ``party_size`` which read as
            # full-party headcount; in a 2-player session the log line said
            # ``party_size=1`` (1 peer) and looked like a count-off-by-one
            # bug to anyone tailing the log without the context. Renamed
            # 2026-05-03 [OBS]; the test in
            # ``tests/server/test_party_peer_identity.py`` accepts both old
            # and new spellings during the cutover so external GM-panel
            # filters keep working until they're audited and updated to
            # the new key.
            logger.info(
                "orchestrator.party_peer_injection peer_count=%d current_player=%s",
                len(context.party_peers),
                context.character_name,
            )
            registry.register_party_peer_section(agent_name, context.party_peers)

        # Chassis interior positions — renders the Ship-tab source of truth
        # into the narrator prompt + the state-patch instruction.
        if context.pc_positions:
            registry.register_chassis_position_section(agent_name, context.pc_positions)

        # Game state (Valley zone)
        if context.state_summary:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "game_state",
                    f"<game_state>\n{context.state_summary}\n</game_state>",
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

        # World context (Valley zone) — persistent across turns.
        # Currently carries the AVAILABLE CULTURES block with
        # ``Culture.chargen=False`` entries filtered out (Story 41-11,
        # closing the Phase 2.2 IOU). Strip the leading newline the
        # helper emits for Rust-style concat — the registry handles
        # section separation.
        if context.world_context:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "world_context",
                    context.world_context.lstrip("\n"),
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

        # Retrieved lore (Valley zone) — Story 37-33. Semantic-search
        # results from the player's action embedded against the lore
        # store. Only registered when a non-empty block was produced;
        # ``None`` means the daemon was unavailable or no fragments
        # cleared the similarity floor, and the prompt stays quiet.
        if context.lore_context:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "retrieved_lore",
                    context.lore_context,
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

        # Magic context (Valley zone) — injected when a world has magic.yaml loaded.
        # Tells the narrator which plugins are active, what the hard_limits are,
        # and the per-actor ledger bars so it can emit magic_working correctly.
        if context.magic_state is not None:
            from sidequest.magic.context_builder import build_magic_context_block

            magic_block = build_magic_context_block(
                magic_state=context.magic_state,
                actor_id=context.character_name or None,
            )
            if magic_block:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "magic_context",
                        f"<magic-context>\n{magic_block}\n</magic-context>",
                        AttentionZone.Valley,
                        SectionCategory.State,
                    ),
                )

        # Active trope summary (Valley zone)
        if context.active_trope_summary:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "active_tropes",
                    context.active_trope_summary,
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

        # SFX library (Valley zone) — static, only on Full tier
        if is_full and context.available_sfx:
            sfx_list = ", ".join(context.available_sfx)
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "sfx_library",
                    (
                        "[AVAILABLE SFX]\n"
                        "When your narration describes a sound-producing action, include matching "
                        "SFX IDs in sfx_triggers. Pick based on what HAPPENED, not what was mentioned.\n"
                        f"Available: {sfx_list}"
                    ),
                    AttentionZone.Valley,
                    SectionCategory.State,
                ),
            )

        # Phase 1 slice: RollOutcome injection deferred to Story 41-6 (dice protocol, Phase 2)

        # Narrator verbosity (Recency zone — every turn)
        registry.register_section(
            agent_name,
            PromptSection.new(
                "narrator_verbosity",
                _build_verbosity_section(context.narrator_verbosity),
                AttentionZone.Recency,
                SectionCategory.Guardrail,
            ),
        )

        # Opening scene constraint (Recency zone, Full tier only)
        if is_full:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "opening_scene_constraint",
                    (
                        "<opening-scene>\n"
                        "This is the OPENING SCENE — the player's first moment in the world.\n"
                        "Set the scene in 3-4 SHORT paragraphs maximum:\n"
                        "1. Where they are (one vivid detail, not a catalogue).\n"
                        "2. What's immediately happening around them.\n"
                        "3. One sensory hook — sound, smell, weather.\n"
                        "4. End with a prompt for their first action (a question, a choice, a threat).\n"
                        "Do NOT write a novel opening. Do NOT describe the world's history. "
                        "Do NOT list every feature of the environment. Drop the player IN and "
                        "let them explore. Under 500 characters of prose total.\n"
                        "MANDATORY: Your game_patch MUST include a visual_scene for this opening "
                        "turn — it is the first illustration the player sees. Use tier "
                        '"landscape" and describe the opening vista.\n'
                        "</opening-scene>"
                    ),
                    AttentionZone.Recency,
                    SectionCategory.Guardrail,
                ),
            )

        # NPC introduction visual constraint (Recency zone, every turn).
        # Playtest 2026-05-03 [BUG] — render policy fired NPC_INTRO for two
        # newly auto-registered NPCs (Inspector Volkova, Drilled door clerk)
        # but the narrator emitted no visual_scene for either, so
        # ``render.eligible_no_subject reason=npc_intro`` warned and the
        # render dispatcher bailed. Two named NPCs introduced in detail with
        # zero portrait or scene image — exactly the OTEL-lie-detector
        # pattern from CLAUDE.md (the prose surface and the visual surface
        # disagreed on whether anything happened).
        #
        # This constraint runs EVERY turn (no ``is_full`` gate) because new
        # NPCs can appear on any turn, not just the opening. The Diamonds
        # and Coal principle (ADR-014) treats first-introduction prose as a
        # diamond — the visual is part of that diamond, not optional. The
        # render trigger policy (server/render_trigger.py) already
        # classifies this turn as NPC_INTRO whenever any NpcMention has
        # ``is_new=True``; this section makes the narrator hold up its end
        # of the contract by always including the matching visual_scene.
        registry.register_section(
            agent_name,
            PromptSection.new(
                "npc_intro_visual_constraint",
                (
                    "<npc-intro-visual>\n"
                    "When you introduce a NEW named NPC for the first time "
                    "this session — i.e. you set ``is_new: true`` on their "
                    "entry in ``npcs_met`` — your game_patch MUST also "
                    "include a ``visual_scene`` whose ``subject`` describes "
                    "that NPC (their appearance, posture, and the moment "
                    "the player is meeting them). Use tier ``\"portrait\"`` "
                    "for a single character close-up, or ``\"landscape\"`` "
                    "when the introduction is inseparable from the place "
                    "(a foreman silhouetted against the rig, a customs "
                    "officer at the freight stair). If multiple NPCs are "
                    "introduced in the same turn, pick the one whose "
                    "introduction carries the most narrative weight — the "
                    "visual is the diamond on that introduction. Recurring "
                    "NPCs (``is_new: false``) do NOT require a fresh "
                    "visual_scene; this rule fires only on the first reveal.\n"
                    "</npc-intro-visual>"
                ),
                AttentionZone.Recency,
                SectionCategory.Guardrail,
            ),
        )

        # Plot-a-course (plot-a-course design). The narrator can plot a
        # course to any body in the prompted set; rejection is OTEL-loud
        # and chart-silent. Block is omitted entirely when the world has
        # no orbital tier or the party has no body anchor.
        if context.orbital_content is not None and context.party_body_id:
            from sidequest.orbital.course import (
                _bodies_in_scope,
                compute_courses,
                format_courses_block,
            )

            in_scope = _bodies_in_scope(
                context.orbital_content.orbits,
                context.orbital_scope,
            )
            course_rows = compute_courses(
                orbits=context.orbital_content.orbits,
                party_at=context.party_body_id,
                in_scope_body_ids=in_scope,
                recent_body_mentions=list(context.recent_body_mentions),
                quest_anchors=list(context.quest_anchors),
            )
            block_text = format_courses_block(course_rows)
            if block_text:
                registry.register_section(
                    agent_name,
                    PromptSection.new(
                        "courses",
                        block_text,
                        AttentionZone.Recency,
                        SectionCategory.Guardrail,
                    ),
                )

        # Narrator vocabulary (Late zone, Full tier only)
        if is_full:
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "narrator_vocabulary",
                    _build_vocabulary_section(context.narrator_vocabulary),
                    AttentionZone.Late,
                    SectionCategory.Format,
                ),
            )

        # PacingHint (Late zone, every tier — combat pacing can change
        # mid-session, so per-turn dynamic state must reach Delta tier too).
        # Rust parity: sidequest-agents/src/prompt_framework/mod.rs:89
        # ``register_pacing_section`` filters to PACING_AGENTS = ["narrator"]
        # internally; safe to call unconditionally when a hint is set.
        if context.pacing_hint is not None:
            hint = context.pacing_hint
            registry.register_pacing_section(
                agent_name,
                hint.narrator_directive(),
                hint.escalation_beat,
            )

        # Group B — Local DM decomposer narrator_directives (Recency zone).
        # When the decomposer ran, run its dispatch bank here and inject the
        # aggregated directives as a high-attention section so they land just
        # before the player action (load-bearing, not ambient context).
        # Group G Task 5: ``visible_dispatch_package`` is the redacted view
        # computed at the top of this method — entries flagged
        # ``redact_from_narrator_canonical`` are already gone.
        if visible_dispatch_package is not None:
            from sidequest.agents.subsystems import run_dispatch_bank

            # ``npc_registry`` is required by ``run_npc_agency`` (kw-only,
            # no default). Always include it — even when empty — so the
            # subsystem can invoke without TypeError. The bank filters
            # context keys per subsystem signature so we don't accidentally
            # blast ``npc_registry`` into subsystems that don't accept it.
            bank_context: dict[str, object] = {
                "npc_registry": list(context.npc_registry or []),
            }

            with context.phase_timings.phase("dispatch_bank"):
                bank_result = await run_dispatch_bank(
                    visible_dispatch_package,
                    context=bank_context,
                )

            # Group C — lethality arbitration runs after the bank and before
            # the narrator_directives section is registered, so the arbiter's
            # paired must_narrate / must_not_narrate directives join the bank
            # directives in the same high-attention block. Determinism is
            # the point: the arbiter decides what verdict fires, the narrator
            # only decides how to describe it (spec §4.1).
            arbiter_directives: list[NarratorDirective] = []
            if context.lethality_policy is not None:
                with context.phase_timings.phase("lethality_arbiter"):
                    from sidequest.agents.lethality_arbiter import LethalityArbiter

                    arbiter = LethalityArbiter(policy=context.lethality_policy)
                    l_result = arbiter.arbitrate(
                        package=visible_dispatch_package,
                        bank_result=bank_result,
                        pc_cores_by_player=context.pc_cores_by_player,
                        npc_cores_by_name=context.npc_cores_by_name,
                    )
                    arbiter_directives = l_result.directives

            with context.phase_timings.phase("prompt_build"):
                combined_directives = list(bank_result.directives) + arbiter_directives
                if combined_directives:
                    block = "\n".join(f"- [{d.kind}] {d.payload}" for d in combined_directives)
                    registry.register_section(
                        agent_name,
                        PromptSection.new(
                            "narrator_directives",
                            block,
                            AttentionZone.Recency,
                            SectionCategory.State,
                        ),
                    )
                for key, err in bank_result.errors:
                    logger.warning(
                        "orchestrator.subsystem_error key=%s error=%s",
                        key,
                        err,
                    )

        with context.phase_timings.phase("prompt_build"):
            # Player action (Recency zone — highest attention, every tier)
            if context.merged_player_actions:
                # Multiplayer merged turn (ADR-036 sealed-letter dispatch).
                # Render every PC's declaration on its own line and reiterate
                # the agency rule inline so the LLM sees it adjacent to the
                # action block. Without this, the prior "Laverne says: ..."
                # framing wrapped the whole merged blob in one PC's name and
                # cued the model to generate dialogue for every PC named in
                # the block (2026-04-29 playtest: "Your call, Engineer,"
                # Laverne says — Laverne's player only typed "I look at
                # Shirley", a glance).
                lines = "\n".join(
                    f"- {name} declares: {act}" for name, act in context.merged_player_actions
                )
                player_action_text = (
                    "This turn, the seated players each declared an action "
                    "simultaneously. Resolve them as a single narrative beat:\n"
                    f"{lines}\n\n"
                    "STRICT: Narrate the resolution of these declared actions "
                    "ONLY. Do NOT generate dialogue, internal thoughts, "
                    "decisions, or new physical actions for any PC listed "
                    "above — only what their player declared. NPCs may speak "
                    "and react. PCs may not be made to speak."
                )
            else:
                player_action_text = f"{context.character_name} says: {action}"
            registry.register_section(
                agent_name,
                PromptSection.new(
                    "player_action",
                    player_action_text,
                    AttentionZone.Recency,
                    SectionCategory.Action,
                ),
            )

            prompt_text = registry.compose(agent_name)
            section_count = len(registry.registry(agent_name))
            logger.info(
                "turn.agent_llm.prompt_build section_count=%d",
                section_count,
            )
            # Dashboard Prompt tab consumes `prompt_assembled`. The hub
            # lives in `sidequest.telemetry.watcher_hub` — importing from
            # `sidequest.server.watcher` would drag in uvicorn's logging
            # reconfiguration and break every caplog-based test.
            from sidequest.telemetry.watcher_hub import publish_event as _pub

            # Build per-zone breakdown for the Prompt tab Zone Breakdown
            # bars. The dashboard expects `zones: [{zone, total_tokens,
            # sections: [{name, token_estimate, category}]}]` keyed by the
            # PascalCase zone names that match the dashboard's ZONE_COLORS
            # map (Primacy/Early/Valley/Late/Recency). Per playtest
            # 2026-04-30 #1B the publish shipped only flat aggregates and
            # the dashboard rendered an empty Zone Breakdown body even
            # though the registry had everything needed.
            sections = registry.registry(agent_name)
            _zone_buckets: dict[str, list] = {}
            for s in sections:
                _zone_buckets.setdefault(s.zone.value, []).append(s)
            zones_payload = []
            for zone_name in ("primacy", "early", "valley", "late", "recency"):
                bucket = _zone_buckets.get(zone_name, [])
                if not bucket:
                    continue
                zones_payload.append(
                    {
                        # Title-case to match the dashboard's ZONE_COLORS
                        # keys (Primacy/Early/Valley/Late/Recency).
                        "zone": zone_name.title(),
                        "total_tokens": sum(s.token_estimate() for s in bucket),
                        "sections": [
                            {
                                "name": s.name,
                                "token_estimate": s.token_estimate(),
                                "category": s.category.value,
                            }
                            for s in bucket
                        ],
                    }
                )

            # Rough token estimate from char count (1 token ≈ 4 chars per
            # the standard Claude tokenizer heuristic). Surfaced as
            # `total_tokens` for the dashboard Prompt tab dropdown
            # ("T3 · narrator · 11210 tokens"); the dashboard pre-fix
            # read `total_tokens` and `agent` directly off the event,
            # so we ship `agent` as an alias of `agent_name` to keep
            # both old and new consumers happy (playtest 2026-04-30 #1A).
            _pub(
                "prompt_assembled",
                {
                    "agent_name": agent_name,
                    "agent": agent_name,
                    "turn_number": context.turn_number,
                    "section_count": section_count,
                    "prompt_len": len(prompt_text),
                    "total_tokens": max(1, len(prompt_text) // 4),
                    "tier": str(tier),
                    "zones": zones_payload,
                },
                component="prompt_builder",
            )
        return prompt_text, registry

    # ------------------------------------------------------------------
    # Main turn entrypoint
    # ------------------------------------------------------------------

    async def run_narration_turn(
        self,
        action: str,
        context: TurnContext,
        *,
        room: object | None = None,
    ) -> NarrationTurnResult:
        """Process a player action through the Phase 1 narration pipeline.

        Routes to the streaming path when SIDEQUEST_NARRATOR_STREAMING=1,
        otherwise delegates to the synchronous path (default, flag-off behavior
        is byte-identical to prior implementation).

        Args:
            action: Raw player input text.
            context: Turn context (world state, genre prompts, etc.).
            room: Optional SessionRoom for streaming delta fan-out. Only
                  consumed by the streaming path; the sync path ignores it.
        """
        if is_streaming_enabled():
            return await self._run_narration_turn_streaming(action, context, room=room)
        return await self._run_narration_turn_synchronous(action, context)

    async def _run_narration_turn_streaming(
        self,
        action: str,
        context: TurnContext,
        *,
        room: object | None = None,
    ) -> NarrationTurnResult:
        """Streaming variant — broadcasts prose deltas live, emits canonical
        NarrationTurnResult at end-of-stream using the same extraction path
        as the synchronous variant.

        Pipeline:
          action → build_narrator_prompt → send_stream (ClaudeClient)
               → StreamFenceParser (prose deltas → broadcast_delta)
               → extract_structured_from_response on full_text
               → NarrationTurnResult (same shape as sync path)

        Falls back to the synchronous path if the client does not support
        streaming (e.g. Ollama or a test double that only has send_with_session).
        """
        import asyncio
        import uuid

        from sidequest.agents.claude_client import (
            StreamComplete,
            StreamError,
            TextDelta,
        )
        from sidequest.agents.stream_fence import StreamFenceParser
        from sidequest.server.emitters import broadcast_delta
        from sidequest.telemetry.spans import (
            narrator_stream_complete_span,
            narrator_stream_error_span,
            narrator_stream_fence_detected,
            narrator_stream_first_token,
            narrator_stream_start_span,
        )

        # Degrade to synchronous if the client doesn't support send_stream
        # (e.g. Ollama or legacy test doubles). No silent fallback — we log
        # loudly so the discrepancy is visible in the GM panel.
        if not hasattr(self._client, "send_stream"):
            logger.warning(
                "orchestrator.streaming_unsupported — client=%r lacks send_stream; "
                "falling back to synchronous path",
                type(self._client).__name__,
            )
            return await self._run_narration_turn_synchronous(action, context)

        with orchestrator_process_action_span(action_len=len(action)):
            agent_name = self._narrator.name()

            tier = self.select_prompt_tier(context)
            prompt_text, _registry = await self.build_narrator_prompt(action, context, tier=tier)

            with self._session_lock:
                current_session_id = self._narrator_session_id

            is_first_turn = current_session_id is None
            system_prompt_for_establish = prompt_text if is_first_turn else None
            send_prompt = action if is_first_turn else prompt_text

            # Mint a turn_id for delta sequencing.  Use the interaction counter
            # when available so deltas are correlated with the canonical event.
            turn_id: str = str(context.turn_number) if context.turn_number else str(uuid.uuid4())

            seq = 0
            delta_count = 0
            prose_chunks: list[str] = []
            first_token_time: float | None = None

            async def on_prose_delta(chunk: str) -> None:
                nonlocal seq
                prose_chunks.append(chunk)
                if room is not None:
                    await broadcast_delta(
                        turn_id=turn_id,
                        chunk=chunk,
                        seq=seq,
                        room=room,
                    )
                seq += 1

            call_start = time.monotonic()

            async def on_fence(prose_bytes: int) -> None:
                narrator_stream_fence_detected(
                    turn_id=turn_id,
                    prose_bytes_at_fence=prose_bytes,
                    seconds_to_fence=time.monotonic() - call_start,
                )

            parser = StreamFenceParser(on_prose_delta=on_prose_delta, on_fence_detected=on_fence)
            terminal: StreamComplete | StreamError | None = None

            with narrator_stream_start_span(
                turn_id=turn_id,
                prompt_tokens=len(send_prompt) // 4,
                model=NARRATOR_MODEL,
                session_id=current_session_id,
            ):
                try:
                    with (
                        context.phase_timings.phase("narrator_subprocess"),
                        turn_agent_llm_inference_span(
                            model=NARRATOR_MODEL,
                            prompt_len=len(send_prompt),
                        ),
                    ):
                        async for event in self._client.send_stream(
                            prompt=send_prompt,
                            model=NARRATOR_MODEL,
                            session_id=current_session_id,
                            system_prompt=system_prompt_for_establish,
                            allowed_tools=[],
                            env_vars={},
                        ):
                            if isinstance(event, TextDelta):
                                if first_token_time is None:
                                    first_token_time = time.monotonic() - call_start
                                    narrator_stream_first_token(
                                        turn_id=turn_id, ttft_seconds=first_token_time
                                    )
                                delta_count += 1
                                await parser.feed(event.text)
                            elif isinstance(event, (StreamComplete, StreamError)):
                                terminal = event
                except asyncio.CancelledError:
                    elapsed_s = time.monotonic() - call_start
                    from sidequest.telemetry.spans import narrator_stream_cancelled_span

                    narrator_stream_cancelled_span(
                        turn_id=turn_id,
                        reason="task_cancelled",
                        partial_prose_bytes=len("".join(prose_chunks)),
                    )
                    logger.warning(
                        "CLAUDE CLI STREAMING CANCELLED turn_id=%s elapsed_s=%.2f",
                        turn_id,
                        elapsed_s,
                    )
                    raise
                except Exception as e:
                    elapsed_ms = int((time.monotonic() - call_start) * 1000)
                    narrator_stream_error_span(
                        turn_id=turn_id,
                        error_kind=type(e).__name__,
                        partial_prose_bytes=len("".join(prose_chunks)),
                        total_seconds=elapsed_ms / 1000.0,
                        detail=str(e),
                    )
                    logger.error(
                        "CLAUDE CLI STREAMING FAILED — returning degraded response (ADR-005) "
                        "agent=%s duration_ms=%d error=%s",
                        agent_name,
                        elapsed_ms,
                        e,
                    )
                    return NarrationTurnResult(
                        narration=(
                            f"**{context.current_location}**\n\n"
                            "The world holds its breath for a moment... "
                            "something shifts in the distance, but the moment passes."
                        ),
                        is_degraded=True,
                        agent_name=agent_name,
                        agent_duration_ms=elapsed_ms,
                        prompt_tier=tier,
                        prompt_text=prompt_text,
                        secret_routes=list(self._last_secret_routes),
                    )

                elapsed_ms = int((time.monotonic() - call_start) * 1000)
                result = await parser.finalize()

                # On StreamError, return degraded response with whatever partial
                # prose we collected before the failure.
                if isinstance(terminal, StreamError):
                    narrator_stream_error_span(
                        turn_id=turn_id,
                        error_kind=terminal.kind,
                        partial_prose_bytes=len(result.prose),
                        total_seconds=elapsed_ms / 1000.0,
                        detail=terminal.detail,
                    )
                    logger.error(
                        "CLAUDE CLI STREAM ERROR — returning degraded response "
                        "agent=%s kind=%s duration_ms=%d detail=%s",
                        agent_name,
                        terminal.kind,
                        elapsed_ms,
                        terminal.detail,
                    )
                    partial_prose = (
                        result.prose
                        or terminal.partial_text
                        or (
                            f"**{context.current_location}**\n\n"
                            "The world holds its breath for a moment... "
                            "something shifts in the distance, but the moment passes."
                        )
                    )
                    return NarrationTurnResult(
                        narration=partial_prose,
                        is_degraded=True,
                        agent_name=agent_name,
                        agent_duration_ms=elapsed_ms,
                        prompt_tier=tier,
                        prompt_text=prompt_text,
                        secret_routes=list(self._last_secret_routes),
                    )

            # Emit complete span for successful streaming turn.
            input_tokens = terminal.input_tokens if isinstance(terminal, StreamComplete) else None
            output_tokens = terminal.output_tokens if isinstance(terminal, StreamComplete) else None
            narrator_stream_complete_span(
                turn_id=turn_id,
                total_seconds=elapsed_ms / 1000.0,
                ttft_seconds=first_token_time,
                prose_bytes=len(result.prose),
                delta_count=delta_count,
                json_parse_status=result.status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            # Store session ID from StreamComplete (ADR-066)
            if isinstance(terminal, StreamComplete) and terminal.session_id:
                with self._session_lock:
                    if self._narrator_session_id is None:
                        logger.info(
                            "narrator.session_established — persistent Opus session created "
                            "(streaming) session_id=%s",
                            terminal.session_id,
                        )
                        if context.genre:
                            self._session_genre = context.genre
                    self._narrator_session_id = terminal.session_id

            # Use the full_text from StreamComplete for extraction (authoritative
            # source — avoids double-reconstruction from chunk list).
            raw_response = (
                terminal.full_text
                if isinstance(terminal, StreamComplete)
                else result.prose
                + (
                    f"\n```game_patch\n{result.game_patch_json}\n```"
                    if result.game_patch_json
                    else ""
                )
            )

            logger.info(
                "Claude CLI returned streaming narration len=%d duration_ms=%d "
                "delta_count=%d fence_status=%s",
                len(raw_response),
                elapsed_ms,
                seq,
                result.status,
            )

            # Parse narrator response using the same helper as the sync path.
            with context.phase_timings.phase("narrator_extraction"):
                extraction = extract_structured_from_response(raw_response)

            prose = extraction["prose"]

            # Group G Task 7 — canonical-leak audit (safety net).
            if context.dispatch_package is not None:
                audit_canonical_prose(
                    prose=prose,
                    package=context.dispatch_package,
                    entity_tokens_by_id=self._entity_tokens_for_registry(context),
                )

            if extraction["action_rewrite"] is None:
                logger.warning("action_rewrite absent from extraction (streaming) — using default")

            if extraction["confrontation"]:
                logger.info(
                    "encounter.confrontation_initiated confrontation_type=%s",
                    extraction["confrontation"],
                )

            for bs_dict in extraction["beat_selections"]:
                if isinstance(bs_dict, dict):
                    logger.info(
                        "encounter.agent_beat_selection actor=%s beat_id=%s target=%r",
                        bs_dict.get("actor"),
                        bs_dict.get("beat_id"),
                        bs_dict.get("target"),
                    )

            npc_mentions = [NpcMention.from_value(v) for v in extraction["npcs_present"]]
            beat_selections = [
                BeatSelection.from_dict(d)
                for d in extraction["beat_selections"]
                if isinstance(d, dict)
            ]
            visual_scene: VisualScene | None = None
            if extraction["visual_scene"] and isinstance(extraction["visual_scene"], dict):
                visual_scene = VisualScene.from_dict(extraction["visual_scene"])
            action_rewrite: ActionRewrite | None = None
            if isinstance(extraction["action_rewrite"], dict):
                action_rewrite = ActionRewrite.from_dict(extraction["action_rewrite"])

            return NarrationTurnResult(
                narration=prose,
                is_degraded=False,
                location=extraction["location"],
                scene_mood=extraction["scene_mood"],
                visual_scene=visual_scene,
                confrontation=extraction["confrontation"],
                beat_selections=beat_selections,
                npcs_present=npc_mentions,
                items_gained=extraction["items_gained"]
                if isinstance(extraction["items_gained"], list)
                else [],
                items_lost=extraction.get("items_lost", []),
                items_discarded=extraction.get("items_discarded", []),
                items_consumed=extraction.get("items_consumed", []),
                footnotes=extraction["footnotes"]
                if isinstance(extraction["footnotes"], list)
                else [],
                quest_updates=extraction["quest_updates"]
                if isinstance(extraction["quest_updates"], dict)
                else {},
                sfx_triggers=extraction["sfx_triggers"]
                if isinstance(extraction["sfx_triggers"], list)
                else [],
                action_rewrite=action_rewrite,
                affinity_progress=extraction["affinity_progress"],
                gold_change=extraction["gold_change"],
                lore_established=extraction["lore_established"],
                status_changes=extraction["status_changes"]
                if isinstance(extraction["status_changes"], list)
                else [],
                magic_working=(
                    extraction["magic_working"]
                    if isinstance(extraction.get("magic_working"), dict)
                    else None
                ),
                game_patch_dict=_extract_game_patch_json(raw_response),
                agent_name=agent_name,
                agent_duration_ms=elapsed_ms,
                token_count_in=input_tokens,
                token_count_out=output_tokens,
                prompt_tier=tier,
                prompt_text=prompt_text,
                raw_response_text=raw_response,
                secret_routes=list(self._last_secret_routes),
            )

    async def _run_narration_turn_synchronous(
        self,
        action: str,
        context: TurnContext,
    ) -> NarrationTurnResult:
        """Synchronous narrator pipeline (send_with_session + extraction).

        This is the primary entry point for Story 41-6 server dispatch.

        Pipeline:
          action → build_narrator_prompt → send_with_session (ClaudeClient)
               → extract_structured_from_response → NarrationTurnResult

        Phase 1 slice: Phase 3 combat dispatch is deferred.
        Phase 1 slice: Phase 2 dice routing is deferred.
        If in_combat and a confrontation is active, reaching here still works —
        the narrator handles encounters via beat_selections in the game_patch.

        Port of orchestrator.rs::Orchestrator::process_action (Phase 1 slice).
        """
        with orchestrator_process_action_span(action_len=len(action)):
            agent_name = self._narrator.name()

            tier = self.select_prompt_tier(context)
            prompt_text, registry = await self.build_narrator_prompt(action, context, tier=tier)

            logger.info("Invoking Claude CLI for narration action=%r", action)

            # ADR-066: persistent session (--resume on subsequent turns)
            with self._session_lock:
                current_session_id = self._narrator_session_id

            is_first_turn = current_session_id is None

            # First turn: full prompt is the system prompt; action is the user message.
            # Subsequent turns: only dynamic state + action is sent.
            system_prompt_for_establish = prompt_text if is_first_turn else None
            send_prompt = action if is_first_turn else prompt_text

            with turn_agent_llm_inference_span(
                model=NARRATOR_MODEL,
                prompt_len=len(send_prompt),
            ):
                call_start = time.monotonic()
                try:
                    with context.phase_timings.phase("narrator_subprocess"):
                        response: ClaudeResponse = await self._client.send_with_session(
                            prompt=send_prompt,
                            model=NARRATOR_MODEL,
                            session_id=current_session_id,
                            system_prompt=system_prompt_for_establish,
                            allowed_tools=[],
                            env_vars={},
                        )
                    elapsed_ms = int((time.monotonic() - call_start) * 1000)
                except Exception as e:
                    elapsed_ms = int((time.monotonic() - call_start) * 1000)
                    logger.error(
                        "CLAUDE CLI FAILED — returning degraded response (ADR-005) "
                        "agent=%s duration_ms=%d error=%s",
                        agent_name,
                        elapsed_ms,
                        e,
                    )
                    return NarrationTurnResult(
                        narration=(
                            f"**{context.current_location}**\n\n"
                            "The world holds its breath for a moment... "
                            "something shifts in the distance, but the moment passes."
                        ),
                        is_degraded=True,
                        agent_name=agent_name,
                        agent_duration_ms=elapsed_ms,
                        prompt_tier=tier,
                        prompt_text=prompt_text,
                        secret_routes=list(self._last_secret_routes),
                    )

            # Store session ID from response (ADR-066)
            if response.session_id:
                with self._session_lock:
                    if self._narrator_session_id is None:
                        logger.info(
                            "narrator.session_established — persistent Opus session created "
                            "session_id=%s",
                            response.session_id,
                        )
                        if context.genre:
                            self._session_genre = context.genre
                    self._narrator_session_id = response.session_id

            raw_response = response.text
            logger.info(
                "Claude CLI returned narration len=%d duration_ms=%d",
                len(raw_response),
                elapsed_ms,
            )

            # Parse narrator response
            with context.phase_timings.phase("narrator_extraction"):
                extraction = extract_structured_from_response(raw_response)

            prose = extraction["prose"]

            # Group G Task 7 — canonical-leak audit (safety net).
            # Structural hiding (Task 5) is the primary defense; this is
            # the lie-detector. Pass the ORIGINAL DispatchPackage so the
            # audit knows what was supposed to be hidden. Expected-zero
            # in steady state; any non-zero fire is a hiding-hole bug.
            if context.dispatch_package is not None:
                audit_canonical_prose(
                    prose=prose,
                    package=context.dispatch_package,
                    entity_tokens_by_id=self._entity_tokens_for_registry(context),
                )

            # Warn on missing action_rewrite
            if extraction["action_rewrite"] is None:
                logger.warning(
                    "action_rewrite absent from extraction — using default (empty rewrite)"
                )

            # Log confrontation initiation
            if extraction["confrontation"]:
                logger.info(
                    "encounter.confrontation_initiated confrontation_type=%s",
                    extraction["confrontation"],
                )

            # Log beat selections
            for bs_dict in extraction["beat_selections"]:
                if isinstance(bs_dict, dict):
                    logger.info(
                        "encounter.agent_beat_selection actor=%s beat_id=%s target=%r",
                        bs_dict.get("actor"),
                        bs_dict.get("beat_id"),
                        bs_dict.get("target"),
                    )

            # Build NpcMention list
            npc_mentions = [NpcMention.from_value(v) for v in extraction["npcs_present"]]

            # Build BeatSelection list
            beat_selections = [
                BeatSelection.from_dict(d)
                for d in extraction["beat_selections"]
                if isinstance(d, dict)
            ]

            # Build VisualScene
            visual_scene: VisualScene | None = None
            if extraction["visual_scene"] and isinstance(extraction["visual_scene"], dict):
                visual_scene = VisualScene.from_dict(extraction["visual_scene"])

            # Build ActionRewrite
            action_rewrite: ActionRewrite | None = None
            if isinstance(extraction["action_rewrite"], dict):
                action_rewrite = ActionRewrite.from_dict(extraction["action_rewrite"])

            return NarrationTurnResult(
                narration=prose,
                is_degraded=False,
                location=extraction["location"],
                scene_mood=extraction["scene_mood"],
                visual_scene=visual_scene,
                confrontation=extraction["confrontation"],
                beat_selections=beat_selections,
                npcs_present=npc_mentions,
                items_gained=extraction["items_gained"]
                if isinstance(extraction["items_gained"], list)
                else [],
                items_lost=extraction.get("items_lost", []),
                items_discarded=extraction.get("items_discarded", []),
                items_consumed=extraction.get("items_consumed", []),
                footnotes=extraction["footnotes"]
                if isinstance(extraction["footnotes"], list)
                else [],
                quest_updates=extraction["quest_updates"]
                if isinstance(extraction["quest_updates"], dict)
                else {},
                sfx_triggers=extraction["sfx_triggers"]
                if isinstance(extraction["sfx_triggers"], list)
                else [],
                action_rewrite=action_rewrite,
                affinity_progress=extraction["affinity_progress"],
                gold_change=extraction["gold_change"],
                lore_established=extraction["lore_established"],
                status_changes=extraction["status_changes"]
                if isinstance(extraction["status_changes"], list)
                else [],
                magic_working=(
                    extraction["magic_working"]
                    if isinstance(extraction.get("magic_working"), dict)
                    else None
                ),
                game_patch_dict=_extract_game_patch_json(raw_response),
                agent_name=agent_name,
                agent_duration_ms=elapsed_ms,
                token_count_in=response.input_tokens,
                token_count_out=response.output_tokens,
                prompt_tier=tier,
                prompt_text=prompt_text,
                raw_response_text=raw_response,
                secret_routes=list(self._last_secret_routes),
            )


# ---------------------------------------------------------------------------
# Module-level convenience function (used by tests and server dispatch)
# ---------------------------------------------------------------------------


async def run_narration_turn(
    client: LlmClient,
    session: GameSnapshot,
    genre: GenrePack,
    player_action: str,
    character_name: str | None = None,
    verbosity: str = "standard",
    vocabulary: str = "literary",
    in_combat: bool = False,
    in_chase: bool = False,
    in_encounter: bool = False,
    state_summary: str | None = None,
) -> NarrationTurnResult:
    """Convenience wrapper: build TurnContext from GameSnapshot + GenrePack and run.

    This is the primary integration point for Story 41-6 server dispatch.
    It assembles a TurnContext from the game snapshot and genre pack, then
    delegates to Orchestrator.run_narration_turn().

    Args:
        client: LlmClient (real or mocked) for LLM calls.
        session: Current game snapshot.
        genre: Loaded genre pack (for prompts.yaml injection).
        player_action: Raw player input text.
        character_name: Acting player's character name.
                        Defaults to first character in session, or "Player".
        verbosity: Narrator verbosity setting (concise|standard|verbose).
        vocabulary: Narrator vocabulary setting (accessible|literary|epic).
        in_combat: Whether an encounter is active (combat).
        in_chase: Whether an encounter is active (chase).
        in_encounter: Whether any encounter is active.
        state_summary: Pre-serialized game state summary string.
                       If None, the session is serialized via model_dump_json().

    Returns:
        NarrationTurnResult with narration and extracted game_patch fields.
    """
    # Resolve character name
    char_name = character_name
    if char_name is None:
        char_name = session.characters[0].core.name if session.characters else "Player"

    # Build state summary if not provided
    if state_summary is None:
        state_summary = session.model_dump_json(indent=2)

    # Build SFX list from genre audio config
    available_sfx: list[str] = []
    if genre.audio and hasattr(genre.audio, "sfx_library"):
        sfx_lib = genre.audio.sfx_library
        if isinstance(sfx_lib, list):
            available_sfx = [str(getattr(s, "id", s)) for s in sfx_lib]

    # Story 37-36: canonical peer-identity packets for every non-self PC.
    party_peers = [
        PartyPeer.from_character(pc) for pc in session.characters if pc.core.name != char_name
    ]

    # Ship-tab positions: render every PC's current_room (when set) into the
    # narrator prompt so the narrator knows where they are on the chassis
    # interior and can state-patch movements.
    pc_positions: dict[str, str | None] = {
        pc.core.name: pc.current_room
        for pc in session.characters
        if getattr(pc, "current_room", None)
    }

    # Task 18 (dual-track momentum): build per-actor status map from session
    # characters so the live encounter zone can render Status objects per actor.
    statuses_by_actor = {ch.core.name: list(ch.core.statuses) for ch in session.characters}

    # Task 18 (dual-track momentum): capture the one-shot resolution signal
    # before building TurnContext so we can clear it from the snapshot after
    # the orchestrator consumes it.
    pending_signal = session.pending_resolution_signal

    context = TurnContext(
        in_combat=in_combat,
        in_chase=in_chase,
        in_encounter=in_encounter,
        state_summary=state_summary,
        narrator_verbosity=verbosity,
        narrator_vocabulary=vocabulary,
        genre=session.genre_slug or None,
        genre_prompts=genre.prompts,
        character_name=char_name,
        turn_number=session.turn_manager.interaction,
        current_location=session.location or "Unknown",
        available_sfx=available_sfx,
        npc_registry=list(session.npc_registry),
        npcs=list(session.npcs),
        chassis_registry=dict(session.chassis_registry),
        party_peers=party_peers,
        pc_positions=pc_positions,
        statuses_by_actor=statuses_by_actor,
        pending_resolution_signal=pending_signal,
        magic_state=session.magic_state,
    )

    orchestrator = Orchestrator(client=client)
    try:
        return await orchestrator.run_narration_turn(player_action, context)
    finally:
        # Clear the one-shot signal after consumption — even if the orchestrator
        # raised. TurnContext is a local copy; the mutation must happen on the
        # session snapshot directly. This mirrors Rust's
        # ``snapshot.pending_resolution_signal.take()`` but is hoisted here
        # because the snapshot is not in scope inside build_narrator_prompt.
        # Must run in a finally: a transient orchestrator failure (LLM timeout,
        # validation error, anything below the degraded-response catch) would
        # otherwise leave the signal in place and re-fire the [ENCOUNTER
        # RESOLVED] zone + consume span on the next turn.
        if pending_signal is not None:
            session.pending_resolution_signal = None
