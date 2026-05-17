"""Beneath Sünden Plan 7 session-integration — the live wiring seam.

The only new production seam (spec Decision 5 / Approach A). Two
functions called from exactly two one-line incisions in the WS session
lifecycle: register the merged look-ahead worker for the session's life,
and bootstrap the Seed=Expansion-0 dungeon on the first open of a
campaign. All dungeon/bootstrap/dep-resolution complexity is isolated
here so the hot session subsystem stays thin.

No Silent Fallbacks: every unresolved dep raises loudly; the genre/world
gate returns None (a clean no-op) only for worlds this dungeon does not
apply to.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from sidequest.agents.llm_factory import build_llm_client
from sidequest.dungeon.lookahead_worker import (
    LookaheadWorkerHandle,
    register_lookahead_worker,
)
from sidequest.dungeon.materializer import materialize
from sidequest.dungeon.persistence import DungeonStore
from sidequest.dungeon.seed_bootstrap import (
    build_entrance_seed_graph,
    build_expansion_one_request,
    select_entrance_theme_id,
)
from sidequest.dungeon.themes import load_theme_palette
from sidequest.game.cookbook.loader import load_cookbook

__all__ = [
    "attach_dungeon_to_session",
    "detach_dungeon_from_session",
]

_GENRE = "caverns_and_claudes"
_WORLD = "beneath_sunden"
# 63-bit seed: positive, fits a SQLite INTEGER, ample entropy.
_SEED_BITS = 63


def _theme_pack_root(world_dir: Path) -> Path:
    """The genre-pack dir holding ``themes/`` (Plan 4 layout
    ``genre_packs/<genre>/themes/``). ``world_dir`` is
    ``…/genre_packs/<genre>/worlds/<world>`` → parents[1] is the pack
    root. Verified loud by load_theme_palette (raises if themes/ absent).
    """
    return world_dir.parent.parent


async def attach_dungeon_to_session(
    *,
    store: Any,
    snapshot: Any,
    genre_pack: Any,
    genre_slug: str,
    world_slug: str,
    world_dir: Path,
) -> LookaheadWorkerHandle | None:
    """Register the look-ahead worker for this session; bootstrap the
    seed on a fresh campaign. Returns the handle (held by the session for
    teardown), or ``None`` for any non-beneath_sunden session (clean
    no-op — the gate lives here so the call site is unconditional)."""
    if genre_slug != _GENRE or world_slug != _WORLD:
        return None

    conn = store.connection()
    persistence = DungeonStore(conn)
    persistence.ensure_schema()  # outside any txn (executescript implicit COMMIT)

    bundle = load_cookbook(world_dir)
    palette = load_theme_palette(_theme_pack_root(world_dir))
    claude_client = build_llm_client()

    # Save-is-truth: reuse a frozen seed; only generate+persist on a
    # genuinely fresh save (a prior failed bootstrap left the seed but no
    # map → reuse it so the retry is deterministic).
    campaign_seed = persistence.get_campaign_seed()
    if campaign_seed is None:
        campaign_seed = secrets.randbits(_SEED_BITS)
        persistence.set_campaign_seed(campaign_seed)
        conn.commit()

    already_seeded = bool(persistence.load_map(entrance_id="entrance").nodes)
    if not already_seeded:
        entrance_theme = select_entrance_theme_id(palette)
        seed_graph = build_entrance_seed_graph(entrance_theme)
        request = build_expansion_one_request(campaign_seed=campaign_seed)
        # The merged commit stage seeds Expansion 0 (entrance) before
        # expansion 1 and rolls back on PersistError (Seed=Expansion-0,
        # spec §6). A bootstrap failure raises loudly here — the connect
        # handler must not start a beneath_sunden session with a broken
        # dungeon (No Silent Fallbacks, spec §9).
        await materialize(
            request,
            graph=seed_graph,
            bundle=bundle,
            palette=palette,
            persistence=persistence,
            snapshot=snapshot,
            pack_tropes=genre_pack,
            claude_client=claude_client,
        )

    return register_lookahead_worker(
        persistence=persistence,
        bundle=bundle,
        palette=palette,
        pack_tropes=genre_pack,
        claude_client=claude_client,
        campaign_seed=campaign_seed,
    )


async def detach_dungeon_from_session(
    handle: LookaheadWorkerHandle | None,
) -> None:
    """Teardown: unregister the observer and drain in-flight look-ahead
    tasks. Null-safe and unconditional-call-safe (handle is None for
    non-beneath_sunden sessions). Does NOT close the connection — the
    room owns the store lifecycle (spec §8 / dossier §9)."""
    if handle is None:
        return
    handle.unregister()
    await handle.drain()
