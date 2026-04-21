"""Culture reference for the narrator prompt.

Resolves the culture list available for a connection and renders the
``=== AVAILABLE CULTURES ===`` block that gets injected into the
narrator prompt's Valley zone. World-tier precedence — when
``world.cultures`` is non-empty, use that; otherwise fall back to
``pack.cultures``. Mirrors the opening-hook resolution pattern in
:mod:`sidequest.server.dispatch.opening_hook`.

Phase 2.2 IOU (ADR-082 ``docs/plans/phase-2-chargen-port.md``): the
``Culture.chargen`` flag was accepted as pass-through in Phase 1 and must
become load-bearing in Phase 2. Cultures with ``chargen: false`` are
lore-only (e.g. subjugated peoples, dead civilisations) — the narrator
must not offer them as player-selectable cultures. This module filters
them out of the injected reference so they stop at the prompt boundary.

Rust source: ``sidequest-api/crates/sidequest-server/src/npc_context.rs::build_culture_reference``.
The Rust implementation does **not** apply the ``chargen`` filter; this
port closes that latent bug as it lands the IOU.
"""

from __future__ import annotations

from collections.abc import Iterable

from sidequest.genre.models.culture import Culture
from sidequest.genre.models.pack import GenrePack

_HEADER = "=== AVAILABLE CULTURES ==="


def build_culture_reference(cultures: Iterable[Culture]) -> str:
    """Render the narrator-facing culture reference block.

    Filters to ``c.chargen is True`` (the model default) so lore-only
    cultures never reach the narrator. Returns an empty string when the
    filtered list is empty — callers should skip injection in that case
    rather than emit an empty header.

    The header is prefixed with a leading newline to match Rust
    (``"\\n=== AVAILABLE CULTURES ==="``) so the block concatenates
    cleanly onto an existing ``world_context`` string.
    """
    eligible = [c for c in cultures if c.chargen]
    if not eligible:
        return ""
    lines = [f"\n{_HEADER}"]
    for c in eligible:
        lines.append(f"- {c.name} — {c.description}")
    return "\n".join(lines)


def resolve_culture_reference(pack: GenrePack, world_slug: str) -> str:
    """Resolve the culture reference for a connection.

    World-tier when ``pack.worlds[world_slug].cultures`` is non-empty,
    else genre-tier ``pack.cultures``. Empty string when both tiers are
    empty or every culture is filtered out.

    Rust parity: ``connect.rs:1285-1295`` (the ``cultures`` lookup +
    ``build_culture_reference`` call inside the chargen-init path).
    """
    world = pack.worlds.get(world_slug)
    if world is not None and world.cultures:
        return build_culture_reference(world.cultures)
    return build_culture_reference(pack.cultures)
