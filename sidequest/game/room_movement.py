"""Room-graph movement — Slice E ports the chargen-time init surface only.

Port of ``sidequest-api/crates/sidequest-game/src/room_movement.rs``
restricted to :func:`init_room_graph_location`. The runtime movement
surface (``validate_room_transition``, ``apply_validated_move``,
``build_room_graph_explored``) belongs to the per-turn dispatch
pipeline and lands with the narrator runtime in a later story.

No silent fallback: a room graph declared ``room_graph`` mode but
carrying no room tagged ``entrance`` is an authoring bug. We raise
:class:`RoomGraphInitError`; the dispatch caller decides whether to
log-and-continue (chargen must not hard-fail) or propagate.
"""

from __future__ import annotations

from sidequest.game.session import GameSnapshot
from sidequest.genre.models.world import RoomDef


class RoomGraphInitError(Exception):
    """Raised when :func:`init_room_graph_location` cannot find an entrance."""


def init_room_graph_location(snap: GameSnapshot, rooms: list[RoomDef]) -> str:
    """Set ``snap.location`` to the graph's entrance room.

    Mutates ``snap`` in place:

    - ``snap.location`` = the first room with ``room_type == "entrance"``
    - ``snap.discovered_rooms`` gains that room id (dedup-append; the
      Rust field is a ``HashSet<String>`` but the Python port keeps
      ``list[str]`` for JSON parity with existing saves).

    Returns the chosen entrance id so the caller can emit OTEL without
    re-scanning ``snap.location``.

    Raises :class:`RoomGraphInitError` when no room is tagged
    ``entrance`` — a pack authoring error rather than a runtime state.
    """
    entrance = next((r for r in rooms if r.room_type == "entrance"), None)
    if entrance is None:
        raise RoomGraphInitError(
            f"room graph has no entrance room — {len(rooms)} rooms checked"
        )

    snap.location = entrance.id
    if entrance.id not in snap.discovered_rooms:
        snap.discovered_rooms.append(entrance.id)
    return entrance.id


__all__ = ["RoomGraphInitError", "init_room_graph_location"]
