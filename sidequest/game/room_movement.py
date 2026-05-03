"""Room-graph movement — Slice E provides the chargen-time init surface only.

This module exposes :func:`init_room_graph_location`. The runtime
movement surface (``validate_room_transition``, ``apply_validated_move``,
``build_room_graph_explored``) belongs to the per-turn dispatch pipeline
and lands with the narrator runtime in a later story.

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
    - ``snap.discovered_rooms`` gains that room id (dedup-append on a
      ``list[str]`` for JSON stability with existing saves).

    Returns the chosen entrance id so the caller can emit OTEL without
    re-scanning ``snap.location``.

    Raises :class:`RoomGraphInitError` when no room is tagged
    ``entrance`` — a pack authoring error rather than a runtime state.
    """
    entrance = next((r for r in rooms if r.room_type == "entrance"), None)
    if entrance is None:
        raise RoomGraphInitError(f"room graph has no entrance room — {len(rooms)} rooms checked")

    snap.location = entrance.id
    if entrance.id not in snap.discovered_rooms:
        snap.discovered_rooms.append(entrance.id)
    return entrance.id


def process_room_entry(
    snap: GameSnapshot,
    *,
    character_id: str,
    room_id: str,
    current_turn: int,
) -> None:
    """Post-room-entry hook: dispatch any rig-coupled auto-fire confrontations
    eligible at the entered room.

    ``room_id`` is chassis-scoped — ``"<chassis_id>:<room_local>"``. Rooms
    that aren't chassis-scoped (no colon, or colon-prefix not matching a
    chassis_registry key) are no-ops on this path; map-graph rooms are
    handled by the legacy room-graph machinery.

    Story 47-4 (Rig MVP Phase C): wires Galley entry → ``the_tea_brew``.
    Iterates ``snap.world_confrontations`` filtered by the chassis's
    interior_rooms, evaluates ``fire_conditions`` (room match,
    bond_tier_min, cooldown_turns), and dispatches eligible firings via
    ``apply_mandatory_outputs`` on the ``clear_win`` branch — the auto-fire
    default; the narrator may override to ``refused`` later through
    explicit dispatch (out of slice scope).

    OTEL: span emission happens inside ``apply_mandatory_outputs`` when
    the rig framing keys are present, plus inside the bond/lineage
    handlers themselves.
    """
    # Resolve room_id to (chassis_id, room_local_id). Two formats accepted:
    #   1. Chassis-prefixed: "<chassis_id>:<room_local>" — explicit form used
    #      by tests and by callers that already know the chassis context.
    #   2. Bare world-name: "Galley", "Cockpit" — narrator-emitted location
    #      strings. Resolved against `chassis.interior_rooms` (case-
    #      insensitive). World-locations that match no chassis interior room
    #      are non-rig and silent no-op on this path; map-graph rooms are
    #      handled by the legacy room-graph machinery.
    if ":" in room_id:
        chassis_id, room_local_id = room_id.split(":", 1)
        chassis = snap.chassis_registry.get(chassis_id)
        if chassis is None:
            return
    else:
        room_local_id = room_id.strip().lower().replace(" ", "_")
        chassis = None
        for c in snap.chassis_registry.values():
            normalized = {r.lower() for r in c.interior_rooms}
            if room_local_id in normalized:
                chassis = c
                break
        if chassis is None:
            return

    bond = chassis.bond_for(character_id)
    if bond is None:
        return

    from sidequest.magic.confrontations import find_eligible_room_autofire
    from sidequest.magic.outputs import apply_mandatory_outputs

    cooldown_view: dict[tuple[str, str], int] = {}
    for key, turn in snap.chassis_autofire_cooldowns.items():
        if ":" not in key:
            continue
        c_id, conf_id = key.split(":", 1)
        cooldown_view[(c_id, conf_id)] = turn

    eligible = find_eligible_room_autofire(
        confrontations=snap.world_confrontations,
        chassis_id=chassis.id,
        room_local_id=room_local_id,
        bond_tier_chassis=bond.bond_tier_chassis,
        current_turn=current_turn,
        cooldown_ledger=cooldown_view,
    )

    for cdef in eligible:
        outputs = cdef.outcomes["clear_win"].mandatory_outputs
        apply_mandatory_outputs(
            snapshot=snap,
            outputs=outputs,
            actor=character_id,
            chassis_id=chassis.id,
            confrontation_id=cdef.id,
            register=cdef.register or "",
            branch="clear_win",
            turn_id=current_turn,
            narrative_seed=f"auto_fire:{room_local_id}",
        )
        snap.chassis_autofire_cooldowns[f"{chassis.id}:{cdef.id}"] = current_turn


__all__ = ["RoomGraphInitError", "init_room_graph_location", "process_room_entry"]
