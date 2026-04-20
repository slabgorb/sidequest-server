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
"""

from __future__ import annotations

import logging
import random

from sidequest.genre.models.narrative import OpeningHook
from sidequest.genre.models.pack import GenrePack

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


def _render_directive(hook: OpeningHook) -> str:
    """Render an OpeningHook into the narrator-prompt directive block.

    Rust parity: connect.rs:1314-1321 —
    ``format!("=== OPENING SCENARIO ===\\nArchetype: {}\\nSituation: {}\\nTone: {}", ...)``
    followed by an optional ``AVOID`` line and the closing marker. String
    format preserved verbatim so GM-panel/content-audit regex on the
    directive keeps matching across the port.
    """
    parts = [
        "=== OPENING SCENARIO ===",
        f"Archetype: {hook.archetype}",
        f"Situation: {hook.situation}",
        f"Tone: {hook.tone}",
    ]
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
    directive = _render_directive(hook)

    logger.info(
        "opening_hook_selected genre=%s world=%s source_tier=%s hook_id=%s archetype=%s",
        genre_slug,
        world_slug,
        source_tier,
        hook.id,
        hook.archetype,
    )

    return hook.first_turn_seed, directive
