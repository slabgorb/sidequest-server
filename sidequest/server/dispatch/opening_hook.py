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

from sidequest.genre.models.narrative import OpeningHook
from sidequest.genre.models.pack import GenrePack
from sidequest.server.session_helpers import _resolve_location_display

logger = logging.getLogger(__name__)


def _resolve_opening_hook(
    pack: GenrePack, world_slug: str, rng: random.Random
) -> tuple[OpeningHook, str] | None:
    """Pick a random opening hook from the world tier or the genre tier.

    Returns ``(hook, source_tier)`` where ``source_tier`` is either
    ``"world"`` or ``"genre"``. Returns ``None`` when neither tier has
    any openings configured — the pack isn't using the opening-hook
    system and the first narrator turn runs without a directive.

    Rust parity: connect.rs:1297-1311.
    """
    world = pack.worlds.get(world_slug)
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


def resolve_opening(
    pack: GenrePack,
    world_slug: str,
    genre_slug: str,
    rng: random.Random | None = None,
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

    Returns:
        ``(opening_seed, opening_directive)`` when a hook is available,
        ``None`` when neither the world nor genre tier declare openings.
        Keeping both fields together in the tuple enforces the "they go
        together or neither goes" invariant — a caller that has a seed
        without a directive is incoherent.

    Rust parity: connect.rs:1301-1336.
    """
    rng = rng if rng is not None else random.Random()

    resolution = _resolve_opening_hook(pack, world_slug, rng)
    if resolution is None:
        return None

    hook, source_tier = resolution

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
