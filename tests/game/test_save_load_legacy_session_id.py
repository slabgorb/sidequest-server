"""ADR-098: saves carrying legacy narrator_session_id load with the field ignored."""

from __future__ import annotations


def test_legacy_save_with_narrator_session_id_loads():
    """A save dict containing narrator_session_id deserializes without error.

    Per ADR-098 spec — saves are exploratory; no migration script. The model
    must tolerate the extra field on load.

    GameSnapshot uses ``model_config = {"extra": "ignore"}`` and never
    declared narrator_session_id as a field, so legacy saves carrying it
    must round-trip cleanly.
    """
    from sidequest.game.session import GameSnapshot

    legacy_payload = {
        # Minimum valid payload — all fields have defaults on GameSnapshot
        # so an empty dict is technically valid, but include the legacy field
        # to prove it is silently dropped.
        "genre_slug": "caverns_and_claudes",
        "world_slug": "flickering_reach",
        "narrator_session_id": "abc-123-legacy",  # extra field from old saves
    }
    save = GameSnapshot.model_validate(legacy_payload)
    assert save is not None
    # The field must NOT round-trip back into the model.
    assert not hasattr(save, "narrator_session_id")
