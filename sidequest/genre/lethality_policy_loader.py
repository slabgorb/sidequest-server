"""Strict YAML loader for per-pack lethality_policy.yaml.

Fails loud per CLAUDE.md "no silent fallbacks":
  - Missing file → LethalityPolicyMissingError (not a warning, not a default)
  - Schema violation → pydantic ValidationError (extra='forbid' catches typos)
  - genre_key/dirname mismatch → ValueError (prevents copy-paste drift)
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sidequest.genre.models.lethality import LethalityPolicy


class LethalityPolicyMissingError(FileNotFoundError):
    """Raised when a genre pack directory has no `lethality_policy.yaml`."""

    def __init__(self, pack_dir: Path) -> None:
        self.pack_dir = pack_dir
        super().__init__(f"lethality_policy.yaml missing in {pack_dir}")


def load_lethality_policy(pack_dir: Path) -> LethalityPolicy:
    """Load + validate the lethality policy for a genre pack.

    `pack_dir` is the directory containing the pack's YAML files — e.g.,
    `sidequest-content/genre_packs/caverns_and_claudes`. Its name is
    cross-checked against the YAML's `genre_key` field.
    """
    path = pack_dir / "lethality_policy.yaml"
    if not path.exists():
        raise LethalityPolicyMissingError(pack_dir)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    policy = LethalityPolicy.model_validate(raw)
    # Accept either the literal dir name or its symlink-resolved target so
    # test fixtures that alias every genre slug to a frozen pack (e.g.
    # tests/fixtures/packs/*/ → test_genre/) don't trip the mismatch guard.
    # On real content (no symlinks) both names are identical and the check
    # is equivalent to the original ``policy.genre_key != pack_dir.name``.
    expected = {pack_dir.name, pack_dir.resolve().name}
    if policy.genre_key not in expected:
        raise ValueError(
            f"genre_key mismatch: yaml says {policy.genre_key!r}, pack dir is {pack_dir.name!r}"
        )
    return policy


__all__ = ["LethalityPolicyMissingError", "load_lethality_policy"]
