"""S1 step 4 — world_confrontations field is gone from GameSnapshot.

Any remaining reference surfaces as AttributeError. The ``model_config =
{"extra": "ignore"}`` setting on GameSnapshot already covers
forward-compat for legacy saves; the migration in Task 4 strips the field
on load before pydantic sees it."""

from __future__ import annotations

import json

import pytest

from sidequest.game.session import GameSnapshot


def test_world_confrontations_attribute_does_not_exist() -> None:
    snap = GameSnapshot(genre_slug="g", world_slug="w")
    with pytest.raises(AttributeError):
        _ = snap.world_confrontations  # type: ignore[attr-defined]


def test_legacy_save_with_world_confrontations_loads_clean() -> None:
    """Saved JSON containing the legacy field must round-trip via the
    migration without breaking validation. The model_config extra=ignore
    + the migration strip combine to make this safe."""
    legacy_json = json.dumps({
        "genre_slug": "g",
        "world_slug": "w",
        "world_confrontations": [],  # legacy field
    })
    from sidequest.game.migrations import migrate_legacy_snapshot

    migrated = migrate_legacy_snapshot(json.loads(legacy_json))
    snap = GameSnapshot.model_validate(migrated)

    assert snap.genre_slug == "g"
    # Confirm the legacy field did not leak in via extra=ignore.
    with pytest.raises(AttributeError):
        _ = snap.world_confrontations  # type: ignore[attr-defined]
