"""Per-session magic-state initialization.

Phase 4 cut-point requires that, at chargen confirmation, a Coyote-Star
session lands with ``snapshot.magic_state`` populated and the freshly
built character already added to the ledger so per-character bars
(sanity / notice / vitality) can mutate from the very first turn.

This module owns the wire path:

    genre pack source_dir + world_slug + character.core.name
        ↓
    load_world_magic(genre_yaml, world_yaml)  →  WorldMagicConfig
        ↓
    MagicState.from_config(config) + add_character(character_id)
        ↓
    snapshot.magic_state

The function fails closed: any LoaderError is logged loud (CLAUDE.md
"No Silent Fallbacks") and ``snapshot.magic_state`` is left at its
prior value (typically ``None``). It does NOT raise — chargen has
already produced a character, and refusing to confirm because magic
config is malformed would orphan the commit.

Worlds without a ``magic.yaml`` (the common case for genres that don't
model magic) skip silently — the absence of the file is a deliberate
authoring decision, not a config error.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sidequest.game.session import GameSnapshot
from sidequest.genre.magic_loader import LoaderError, load_world_magic
from sidequest.magic.state import MagicState

logger = logging.getLogger(__name__)


def init_magic_state_for_session(
    *,
    snapshot: GameSnapshot,
    genre_pack_source_dir: Path | None,
    world_slug: str,
    character_id: str,
) -> bool:
    """Populate ``snapshot.magic_state`` for the freshly chargen'd character.

    Returns True iff a magic state was loaded and assigned. Returns False
    when:
      - ``genre_pack_source_dir`` is None (pack loaded from a non-disk
        source — magic loader requires file paths)
      - genre or world ``magic.yaml`` is absent (this world has no magic)
      - loader raised LoaderError (logged at ERROR; snapshot untouched)
    """
    if genre_pack_source_dir is None:
        return False

    genre_magic = genre_pack_source_dir / "magic.yaml"
    world_magic = genre_pack_source_dir / "worlds" / world_slug / "magic.yaml"

    if not genre_magic.exists() or not world_magic.exists():
        # No magic config for this world — silent, expected, common.
        return False

    try:
        config = load_world_magic(genre_yaml=genre_magic, world_yaml=world_magic)
    except LoaderError as exc:
        # Authoring bug — log loud per CLAUDE.md, don't crash chargen.
        logger.error(
            "magic.init_failed world=%s genre_yaml=%s world_yaml=%s error=%s",
            world_slug,
            genre_magic,
            world_magic,
            exc,
        )
        return False

    # Pingpong 2026-04-30 ("Magic system parse_error: unknown actor:
    # 'Linus'; call add_character first"): in 4P MP each player's
    # chargen confirmation calls this function; pre-fix every call did
    # ``MagicState.from_config(config)`` which built a NEW state with
    # only the current ``character_id`` and assigned it to
    # ``snapshot.magic_state``, wiping prior committers. With four
    # sequential commits Charlie → Snoopy → Linus → Lucy, only Lucy
    # ended up in the ledger; the next turn's narrator referenced
    # Linus by name, the magic parser tried to apply a working against
    # actor 'Linus', the per-character bars weren't there, and the
    # parser raised ``unknown actor: 'Linus'; call add_character first``.
    # Fix: idempotent on the snapshot — if a magic state already
    # exists for this slug, REUSE it and just register the new
    # character. Build a fresh state only on the first commit (when
    # ``snapshot.magic_state is None``). Mirrors the same MP-aware
    # idempotence the lore seeders rely on (DuplicateLoreId guard) and
    # the canonical-snapshot model (room-owned shared world state).
    if snapshot.magic_state is None:
        state = MagicState.from_config(config)
        snapshot.magic_state = state
        first_commit = True
    else:
        state = snapshot.magic_state
        first_commit = False
    state.add_character(character_id)
    logger.info(
        "magic.init world=%s actor=%s plugins=%s bars=%d first_commit=%s",
        world_slug,
        character_id,
        list(config.active_plugins),
        len(state.ledger),
        first_commit,
    )
    return True
