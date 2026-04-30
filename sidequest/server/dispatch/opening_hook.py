"""Opening-hook resolution — picks a random ``OpeningHook`` and renders
the narrator directive injected into the first Playing-state turn.

Port of the opening-hook selection block in
``sidequest-api/crates/sidequest-server/src/dispatch/connect.rs``
(lines ~1297-1336): the Rust server resolves openings once per
connection and stashes ``opening_seed`` + ``opening_directive`` on the
connection-scoped state. The first narrator turn consumes both — the
seed becomes the action string, the directive is injected into the
Early zone on turn 0 only.

World-tier precedence: when ``world.openings`` is non-empty, pick from
there; otherwise fall back to ``pack.openings``. Mirrors the ``cultures``
lookup pattern used for name banks.

Multiplayer precedence (post-Rust, playtest 2026-04-30): when the
session is in multiplayer mode AND the world declares one or more
``mp_openings`` (loaded from ``worlds/{slug}/mp_opening.yaml``), pick
from those first. MP openings land the party aboard the world's
flagship rig in a chill setting — no turn-1 confrontation, no dice,
every PC gets a voice slot. Falls back to the standard precedence
chain when the world has no mp_openings authored or the session is
solo. See ``coyote_star/mp_opening.yaml`` for the canonical shape.

Setting injection (playtest 2026-04-30 "Setting drift"): when the world
declares ``starting_location`` (and chargen prose has built up reader
expectations around it), the directive carries a ``Setting:`` line so
the narrator's first turn lands at the world's authored opening
location instead of free-styling somewhere else. Pre-fix, Coyote
Reach's chargen close said "Far Landing is just waking up around you,
Begin" but turn 1 opened at "Turning Hub — Docking Crescent" — a
Diamonds-and-Coal violation (chargen baited the hook, turn 1 yanked
the rod). The directive now passes the starting_location through to the
narrator agent.
"""

from __future__ import annotations

import logging
import random

from sidequest.game.persistence import GameMode
from sidequest.genre.models.narrative import MpOpening, OpeningHook
from sidequest.genre.models.pack import GenrePack
from sidequest.server.session_helpers import _resolve_location_display

logger = logging.getLogger(__name__)


def _resolve_opening_hook(
    pack: GenrePack,
    world_slug: str,
    rng: random.Random,
    *,
    mode: GameMode | None = None,
) -> tuple[OpeningHook | MpOpening, str] | None:
    """Pick a random opening from the matching tier.

    Returns ``(opening, source_tier)`` where ``source_tier`` is one of
    ``"world_mp"``, ``"world"``, or ``"genre"``. Returns ``None`` when
    no tier has any openings configured — the pack isn't using the
    opening-hook system and the first narrator turn runs without a
    directive.

    When ``mode == MULTIPLAYER`` and the world has ``mp_openings``,
    the MP tier wins. Otherwise falls through to the standard
    world-then-genre OpeningHook precedence (Rust parity:
    connect.rs:1297-1311).
    """
    world = pack.worlds.get(world_slug)

    if (
        mode == GameMode.MULTIPLAYER
        and world is not None
        and world.mp_openings
    ):
        mp = rng.choice(world.mp_openings)
        return mp, "world_mp"

    if world is not None and world.openings:
        hook = rng.choice(world.openings)
        return hook, "world"

    if pack.openings:
        hook = rng.choice(pack.openings)
        return hook, "genre"

    return None


def _render_directive(
    hook: OpeningHook,
    *,
    setting_label: str | None = None,
    starting_time: str | None = None,
) -> str:
    """Render an OpeningHook into the narrator-prompt directive block.

    Rust parity: connect.rs:1314-1321 —
    ``format!("=== OPENING SCENARIO ===\\nArchetype: {}\\nSituation: {}\\nTone: {}", ...)``
    followed by an optional ``AVOID`` line and the closing marker. String
    format preserved verbatim so GM-panel/content-audit regex on the
    directive keeps matching across the port.

    Setting line (post-Rust addition, playtest 2026-04-30): when the
    world declares an authored opening location (``starting_location``
    resolved through cartography), inject a ``Setting:`` line so the
    narrator's first turn honors the chargen close prose's location
    promise. Pure addition — older directives without the line still
    parse identically.
    """
    parts = [
        "=== OPENING SCENARIO ===",
        f"Archetype: {hook.archetype}",
        f"Situation: {hook.situation}",
        f"Tone: {hook.tone}",
    ]
    if setting_label:
        time_suffix = f", {starting_time}" if starting_time else ""
        parts.append(f"Setting: {setting_label}{time_suffix} (open the scene here)")
    if hook.avoid:
        parts.append("AVOID: " + "; ".join(hook.avoid))
    parts.append("=== END OPENING ===")
    return "\n".join(parts)


def _render_mp_directive(mp: MpOpening) -> str:
    """Render an ``MpOpening`` into the narrator-prompt directive block.

    MP openings carry the establishing scene as authored prose; the
    narrator should treat that prose as the scene to play (or close
    paraphrase). Per-PC beats, soft hook, party framing, and the avoid
    list ride along so the narrator has the full intent without
    re-deriving it. The directive is bracketed identically to the
    standard ``=== OPENING SCENARIO === / === END OPENING ===`` form so
    GM-panel/content-audit regex on the directive keeps matching.
    """
    parts: list[str] = ["=== OPENING SCENARIO ===", "Mode: multiplayer"]
    if mp.name:
        parts.append(f"Title: {mp.name}")

    setting = mp.setting or {}
    rig = str(setting.get("rig", "") or "").strip()
    room = str(setting.get("room", "") or "").strip()
    if rig and room:
        parts.append(f"Setting: aboard the {rig}, {room}")
    elif rig:
        parts.append(f"Setting: aboard the {rig}")
    situation = str(setting.get("situation", "") or "").strip()
    if situation:
        parts.append(f"Situation: {situation}")

    tone = mp.tone or {}
    register = str(tone.get("register", "") or "").strip()
    if register:
        parts.append(f"Tone: {register}")
    stakes = str(tone.get("stakes", "") or "").strip()
    if stakes:
        parts.append(f"Stakes: {stakes}")

    if mp.establishing_narration.strip():
        parts.append("ESTABLISHING NARRATION (play this scene):")
        parts.append(mp.establishing_narration.rstrip())

    party = mp.party_framing or {}
    if party:
        framing_lines: list[str] = []
        if party.get("already_a_crew"):
            framing_lines.append("- The PCs are already a crew. Do not re-introduce them to one another.")
        bond = str(party.get("bond_tier_default", "") or "").strip()
        if bond:
            framing_lines.append(f"- Default character-rig bond tier: {bond}.")
        seeds = party.get("shared_history_seeds") or []
        if isinstance(seeds, list) and seeds:
            framing_lines.append("- Shared history seeds:")
            for seed in seeds:
                framing_lines.append(f"  • {seed}")
        guidance = str(party.get("narrator_guidance", "") or "").strip()
        if guidance:
            framing_lines.append(f"- {guidance}")
        if framing_lines:
            parts.append("PARTY FRAMING:")
            parts.extend(framing_lines)

    if mp.rig_voice_seeds:
        parts.append("RIG VOICE SEEDS (use the rig's authored register):")
        for seed in mp.rig_voice_seeds:
            ctx = str(seed.get("context", "") or "").strip()
            line = str(seed.get("line", "") or "").strip()
            if ctx and line:
                parts.append(f"- {ctx}: {line}")
            elif line:
                parts.append(f"- {line}")

    if mp.per_pc_beats:
        parts.append("PER-PC BEATS (offer at most one per PC, keyed to chargen):")
        for beat in mp.per_pc_beats:
            applies_to = beat.get("applies_to") or {}
            beat_text = str(beat.get("beat", "") or "").strip()
            if isinstance(applies_to, dict) and applies_to and beat_text:
                key_pairs = ", ".join(f"{k}={v}" for k, v in applies_to.items())
                parts.append(f"- when [{key_pairs}]: {beat_text}")
            elif beat_text:
                parts.append(f"- {beat_text}")

    soft = mp.soft_hook or {}
    if soft:
        parts.append("SOFT HOOK (only if conversation lulls — do not force):")
        timing = str(soft.get("timing", "") or "").strip()
        if timing:
            parts.append(f"- Timing: {timing}")
        narration = str(soft.get("narration", "") or "").strip()
        if narration:
            parts.append(f"- Narration: {narration}")
        escalation = soft.get("escalation_path") or {}
        if isinstance(escalation, dict):
            for k, v in escalation.items():
                parts.append(f"- Escalation/{k}: {v}")

    avoid_list = []
    avoid_raw = tone.get("avoid_at_all_costs")
    if isinstance(avoid_raw, list):
        avoid_list = [str(a).strip() for a in avoid_raw if str(a).strip()]
    if avoid_list:
        parts.append("AVOID: " + "; ".join(avoid_list))

    if mp.first_turn_invitation.strip():
        parts.append("FIRST TURN INVITATION (close the scene with this):")
        parts.append(mp.first_turn_invitation.rstrip())

    parts.append("=== END OPENING ===")
    return "\n".join(parts)


def _mp_opening_seed(mp: MpOpening) -> str:
    """Pick the action string for the narrator's first turn from an MP opening.

    Prefer the ``first_turn_invitation`` (the closing prompt the narrator
    is meant to land on); fall back to a generic establishing-scene
    instruction. Mirrors how OpeningHook's ``first_turn_seed`` becomes
    the first-turn action in ``websocket_session_handler._run_opening_turn``.
    """
    if mp.first_turn_invitation.strip():
        return mp.first_turn_invitation.strip()
    return "Open the scene as authored. Each PC takes a moment."


def resolve_opening(
    pack: GenrePack,
    world_slug: str,
    genre_slug: str,
    rng: random.Random | None = None,
    *,
    mode: GameMode | None = None,
) -> tuple[str, str] | None:
    """Resolve opening seed + directive for a connection.

    Args:
        pack: Loaded genre pack.
        world_slug: Selected world slug (prefer world-tier openings when
            this world declares any).
        genre_slug: Genre slug — only used for logging.
        rng: Optional seeded RNG for deterministic tests; falls back to
            ``random.Random()`` (default-seeded) when ``None``. Rust uses
            ``rand::rng().random_range(0..openings.len())`` with no
            explicit seeding.
        mode: Game mode — when ``MULTIPLAYER`` and the world has
            ``mp_openings``, the MP tier is selected. Solo / unset
            preserves the legacy world-then-genre precedence.

    Returns:
        ``(opening_seed, opening_directive)`` when a hook is available,
        ``None`` when neither tier declares openings. Keeping both
        fields together in the tuple enforces the "they go together or
        neither goes" invariant — a caller that has a seed without a
        directive is incoherent.

    Rust parity: connect.rs:1301-1336 (solo path); MP path is post-Rust.
    """
    rng = rng if rng is not None else random.Random()

    resolution = _resolve_opening_hook(pack, world_slug, rng, mode=mode)
    if resolution is None:
        return None

    opening, source_tier = resolution

    if isinstance(opening, MpOpening):
        directive = _render_mp_directive(opening)
        seed = _mp_opening_seed(opening)
        logger.info(
            "opening_hook_selected genre=%s world=%s source_tier=%s hook_id=%s mode=mp",
            genre_slug,
            world_slug,
            source_tier,
            opening.id,
        )
        return seed, directive

    # Legacy OpeningHook path — solo or MP without authored mp_openings.
    hook = opening

    # Resolve the world's authored opening location (slug → display name
    # via cartography rooms). When the world has no starting_location,
    # ``setting_label`` is None and the directive omits the Setting line
    # — older worlds keep working unchanged. When the slug is set but
    # cartography lacks an entry for it, the helper falls through to
    # snake-case humanization, which is still readable to the narrator.
    setting_label: str | None = None
    starting_time: str | None = None
    world = pack.worlds.get(world_slug)
    # ``starting_location`` lives on ``World.config`` (a WorldConfig) per
    # sidequest/genre/models/world.py; the World aggregate only carries it
    # transitively. ``starting_time`` is authored on the same world.yaml
    # top level but isn't a typed field — it lands in ``WorldConfig``'s
    # ``model_extra`` (extra="allow"). Read both defensively so a world
    # without either still produces a coherent directive.
    if world is not None:
        cfg = getattr(world, "config", None)
        if cfg is not None:
            starting_location_slug = (cfg.starting_location or "").strip()
            if starting_location_slug:
                resolved = _resolve_location_display(
                    pack, world_slug, starting_location_slug
                )
                setting_label = resolved or None
            extra = getattr(cfg, "model_extra", None) or {}
            if isinstance(extra, dict):
                candidate_time = str(extra.get("starting_time", "") or "").strip()
                starting_time = candidate_time or None

    directive = _render_directive(
        hook,
        setting_label=setting_label,
        starting_time=starting_time,
    )

    logger.info(
        "opening_hook_selected genre=%s world=%s source_tier=%s hook_id=%s archetype=%s setting=%r",
        genre_slug,
        world_slug,
        source_tier,
        hook.id,
        hook.archetype,
        setting_label,
    )

    return hook.first_turn_seed, directive
