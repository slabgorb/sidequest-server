"""Test helper for resolving genre-pack filesystem paths.

Packs may live under either ``sidequest-content/genre_packs/`` (production-
shipping) or ``sidequest-content/genre_workshopping/`` (in development /
graduated out). Tests should not care which root a pack currently lives
under -- that's content-team state, not test contract.

Use ``find_pack_path("heavy_metal")`` to get the actual on-disk directory.
"""
from __future__ import annotations

from pathlib import Path

# tests/_helpers/genre_paths.py -> tests -> sidequest-server -> oq-2 root
_REPO_ROOT = Path(__file__).resolve().parents[3]
CONTENT_ROOT = _REPO_ROOT / "sidequest-content"
GENRE_PACKS_DIR = CONTENT_ROOT / "genre_packs"
GENRE_WORKSHOPPING_DIR = CONTENT_ROOT / "genre_workshopping"


class PackNotFound(FileNotFoundError):
    """Raised when a slug doesn't resolve under either content root."""


def _has_pack_yaml(candidate: Path) -> bool:
    return candidate.is_dir() and (candidate / "pack.yaml").is_file()


def find_pack_path(slug: str) -> Path:
    """Return the filesystem path to a pack by slug.

    Checks ``genre_packs/<slug>/`` first (production), falls back to
    ``genre_workshopping/<slug>/``. Resolution requires ``pack.yaml`` to
    exist in the candidate directory -- bare ``is_dir()`` is insufficient
    because a slug may have residual ``images/``/``worlds/`` subtrees in
    one root after a content-team move (see commit 0b7c311 moving
    heavy_metal and spaghetti_western to genre_workshopping/).

    Raises ``PackNotFound`` if neither root contains a ``pack.yaml`` for
    the slug. **No silent fallback** beyond the two named roots.
    """
    primary = GENRE_PACKS_DIR / slug
    if _has_pack_yaml(primary):
        return primary
    fallback = GENRE_WORKSHOPPING_DIR / slug
    if _has_pack_yaml(fallback):
        return fallback
    raise PackNotFound(
        f"genre pack {slug!r} not found (no pack.yaml under "
        f"{GENRE_PACKS_DIR} or {GENRE_WORKSHOPPING_DIR})"
    )
