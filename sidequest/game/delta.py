"""State delta — captures client-visible changes between two game snapshots.

The game-layer StateDelta is a boolean-flagged change detector used
internally to determine which parts of state changed. It is DISTINCT
from the protocol's ``sidequest.protocol.models.StateDelta``, which
carries actual data values over the wire to the client.

StateSnapshot stores serialized JSON strings per field group so equality
checks are O(1).

The ``compute_delta`` function takes two GameSnapshot instances and
returns a StateDelta indicating which field groups changed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from sidequest.game.session import GameSnapshot


class StateDelta(BaseModel):
    """Boolean flags indicating which game state fields changed between snapshots.

    Used internally for broadcast optimization — avoids sending full
    state every turn. Only changed field groups trigger client updates.

    NOTE: This is NOT the same as sidequest.protocol.models.StateDelta.
    The protocol StateDelta carries wire-format data (location string,
    character list, etc.). This type carries only boolean change flags.
    build_protocol_delta() (in session.py) converts this into the
    protocol type.
    """

    model_config = {"extra": "forbid"}

    characters: bool = False
    npcs: bool = False
    location: bool = False
    time_of_day: bool = False
    quest_log: bool = False
    notes: bool = False
    tropes: bool = False
    atmosphere: bool = False
    regions: bool = False
    routes: bool = False
    active_stakes: bool = False
    lore: bool = False
    new_location: str | None = None

    def is_empty(self) -> bool:
        """True when no field changed."""
        return not (
            self.characters
            or self.npcs
            or self.location
            or self.time_of_day
            or self.quest_log
            or self.notes
            or self.tropes
            or self.atmosphere
            or self.regions
            or self.routes
            or self.active_stakes
            or self.lore
        )

    def characters_changed(self) -> bool:
        return self.characters

    def npcs_changed(self) -> bool:
        return self.npcs

    def location_changed(self) -> bool:
        return self.location

    def quest_log_changed(self) -> bool:
        return self.quest_log

    def atmosphere_changed(self) -> bool:
        return self.atmosphere

    def regions_changed(self) -> bool:
        return self.regions

    def tropes_changed(self) -> bool:
        return self.tropes


class StateSnapshot:
    """Frozen JSON snapshot of game state for delta comparison.

    Uses serialized JSON strings per field group for O(1) equality checks.
    """

    def __init__(self, state: GameSnapshot) -> None:
        self.characters_json = _to_json(state.characters)
        self.npcs_json = _to_json(state.npcs)
        self.location = state.location
        self.time_of_day = state.time_of_day
        self.quest_log_json = _to_json(state.quest_log)
        self.notes_json = _to_json(state.notes)
        self.active_tropes_json = _to_json(state.active_tropes)
        self.atmosphere = state.atmosphere
        self.current_region = state.current_region
        self.discovered_regions_json = _to_json(state.discovered_regions)
        self.discovered_routes_json = _to_json(state.discovered_routes)
        self.active_stakes = state.active_stakes
        self.lore_established_json = _to_json(state.lore_established)


def _to_json(value: object) -> str:
    """Serialize to JSON for snapshot comparison. Returns "" on error."""
    try:
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump())  # type: ignore[attr-defined]
        return json.dumps(value)
    except Exception:
        return ""


def snapshot(state: GameSnapshot) -> StateSnapshot:
    """Take a snapshot of game state for later delta comparison."""
    return StateSnapshot(state)


def compute_delta(before: StateSnapshot, after: StateSnapshot) -> StateDelta:
    """Compute which field groups changed between two state snapshots."""
    location_changed = before.location != after.location
    return StateDelta(
        characters=before.characters_json != after.characters_json,
        npcs=before.npcs_json != after.npcs_json,
        location=location_changed,
        time_of_day=before.time_of_day != after.time_of_day,
        quest_log=before.quest_log_json != after.quest_log_json,
        notes=before.notes_json != after.notes_json,
        tropes=before.active_tropes_json != after.active_tropes_json,
        atmosphere=before.atmosphere != after.atmosphere,
        regions=before.discovered_regions_json != after.discovered_regions_json,
        routes=before.discovered_routes_json != after.discovered_routes_json,
        active_stakes=before.active_stakes != after.active_stakes,
        lore=before.lore_established_json != after.lore_established_json,
        new_location=after.location if location_changed else None,
    )
