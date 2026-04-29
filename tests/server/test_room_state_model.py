"""Unit tests for ``RoomState`` and ``ContainerState`` pydantic models.

Story 45-13: per-room container retrieved-state. The data shape exists
so the apply path (`narration_apply.py`), the prompt-build seam
(`session_helpers._build_turn_context`), and the persistence layer
(`SqliteStore`) can read and write a typed model — not a magic dict.

These are the cheap unit tests that pin the shape. The wire-test
(`test_container_retrieval_state.py`) exercises the apply seam end to
end. Both must pass for AC #1 and AC #5 to land.
"""
from __future__ import annotations

import pytest

# The model module does not exist yet — Dev (45-13 GREEN) will pick the
# location. Per the story context the recommendation is
# ``sidequest/game/room_state.py`` (sibling to other domain files), but
# ``sidequest/game/session.py`` is also acceptable. Tests import via the
# canonical re-export at ``sidequest.game.session`` to insulate against
# the file-layout choice.


def test_container_state_default_unretrieved() -> None:
    """A fresh ``ContainerState`` reports ``retrieved=False`` and no round."""
    from sidequest.game.session import ContainerState

    state = ContainerState(container_id="tin_box")
    assert state.container_id == "tin_box"
    assert state.retrieved is False
    assert state.retrieved_at_round is None


def test_container_state_records_retrieval_round() -> None:
    """``retrieved=True`` pairs with a concrete ``retrieved_at_round`` int.

    The negative-gate logic depends on having a numeric round — AC #2
    requires the blocked-span attribute ``prior_retrieved_at_round`` to
    surface the round number, not a bool.
    """
    from sidequest.game.session import ContainerState

    state = ContainerState(
        container_id="tin_box", retrieved=True, retrieved_at_round=10,
    )
    assert state.retrieved is True
    assert state.retrieved_at_round == 10


def test_room_state_default_empty_containers() -> None:
    """A fresh ``RoomState`` has an empty containers dict — not None."""
    from sidequest.game.session import RoomState

    rs = RoomState(room_id="mawdeep:vault")
    assert rs.room_id == "mawdeep:vault"
    assert rs.containers == {}


def test_room_state_round_trips_through_pydantic_json() -> None:
    """``RoomState`` JSON-roundtrips losslessly.

    The save path serializes ``GameSnapshot`` via ``model_dump_json()``
    and ``SqliteStore.load`` rehydrates via ``model_validate_json``.
    Drift here breaks AC #5.
    """
    from sidequest.game.session import ContainerState, RoomState

    original = RoomState(
        room_id="mawdeep:vault",
        containers={
            "tin_box": ContainerState(
                container_id="tin_box",
                retrieved=True,
                retrieved_at_round=10,
            ),
        },
    )
    payload = original.model_dump_json()
    restored = RoomState.model_validate_json(payload)
    assert restored.room_id == original.room_id
    assert "tin_box" in restored.containers
    assert restored.containers["tin_box"].retrieved is True
    assert restored.containers["tin_box"].retrieved_at_round == 10


def test_game_snapshot_has_room_states_field_default_empty() -> None:
    """``GameSnapshot.room_states`` is a typed dict, defaults to ``{}``.

    A fresh snapshot must expose the field — not raise AttributeError —
    so reads at the prompt-build seam don't have to defensively check
    for the field's existence on every turn.
    """
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    assert hasattr(snap, "room_states")
    assert snap.room_states == {}


def test_game_snapshot_room_states_typed_dict_round_trips() -> None:
    """``room_states`` round-trips through the snapshot's own JSON path.

    AC #5 requires the field to survive ``SqliteStore.save`` →
    ``SqliteStore.load``. This test pins the snapshot-level JSON contract
    that the persistence layer relies on.
    """
    from sidequest.game.session import (
        ContainerState,
        GameSnapshot,
        RoomState,
    )

    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    snap.room_states["mawdeep:vault"] = RoomState(
        room_id="mawdeep:vault",
        containers={
            "tin_box": ContainerState(
                container_id="tin_box",
                retrieved=True,
                retrieved_at_round=10,
            ),
        },
    )
    payload = snap.model_dump_json()
    restored = GameSnapshot.model_validate_json(payload)
    assert "mawdeep:vault" in restored.room_states
    rs = restored.room_states["mawdeep:vault"]
    assert rs.containers["tin_box"].retrieved is True
    assert rs.containers["tin_box"].retrieved_at_round == 10


def test_game_snapshot_load_old_save_without_room_states_field_defaults_empty() -> None:
    """Forward-compat: an old save serialized without ``room_states``
    deserializes with the field defaulting to ``{}``.

    AC #5 explicitly requires this — there is no save migration step
    for 45-13. Old saves simply pick up an empty room_states map. This
    relies on ``GameSnapshot.model_config = {"extra": "ignore"}`` plus
    the field having a default factory.
    """
    from sidequest.game.session import GameSnapshot

    # Minimal old-save JSON — no ``room_states`` key. Pydantic accepts
    # missing fields when they have defaults; the explicit test guards
    # against an accidental ``Field(...)`` (no default) regression.
    legacy_payload = (
        '{"genre_slug": "caverns_and_claudes", "world_slug": "mawdeep", '
        '"location": "vault", "discovered_rooms": ["vault"]}'
    )
    snap = GameSnapshot.model_validate_json(legacy_payload)
    assert snap.room_states == {}


# ---------------------------------------------------------------------------
# Discovered_rooms vs room_states — they answer different questions.
# Per story context: do not collapse them.
# ---------------------------------------------------------------------------


def test_discovered_rooms_and_room_states_are_independent() -> None:
    """A room can be ``discovered_rooms`` without any retrieved containers.

    ``discovered_rooms`` answers "have we been here?"; ``room_states``
    answers "what mechanical state lives here?" Independence prevents
    implementations that paper one onto the other.
    """
    from sidequest.game.session import GameSnapshot

    snap = GameSnapshot(
        genre_slug="caverns_and_claudes", discovered_rooms=["vault", "hall"],
    )
    # Discovery does NOT auto-create a RoomState.
    assert snap.room_states == {}
    # And vice-versa — a future write to room_states must not implicitly
    # backfill discovered_rooms (kept loose here because the "vice-versa"
    # constraint isn't load-bearing for AC #1).


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
