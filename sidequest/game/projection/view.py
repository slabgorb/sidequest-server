"""GameStateView — narrow, read-only projection of session state the filter reads.

Implemented by SessionHandler (SessionGameStateView, added in Task 3).
Filter never mutates. Every method returns None where state is unknown
rather than raising — the filter treats unknown relationships conservatively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class GameStateView(Protocol):
    def is_gm(self, player_id: str) -> bool: ...
    def seat_of(self, player_id: str) -> str | None: ...
    def character_of(self, player_id: str) -> str | None: ...
    def zone_of(self, character_id: str) -> str | None: ...
    def visible_to(self, viewer_character_id: str, target_character_id: str) -> bool: ...
    def owner_of_item(self, item_id: str) -> str | None: ...
    def party_of(self, player_id: str) -> str | None: ...


@dataclass
class SessionGameStateView:
    """Conservative GameStateView implementation.

    Phase-3 engine state does not yet track zones, per-item ownership, or
    detailed visibility. This adapter returns None / False for those
    relationships — which for redaction rules means the field stays
    masked. That is the safe direction: a missing relationship must never
    unmask a field.

    Fields can be populated incrementally as engine state grows.
    """

    gm_player_id: str | None
    player_id_to_character: dict[str, str] = field(default_factory=dict)
    party_id: str | None = None
    seat_assignments: dict[str, str] = field(default_factory=dict)

    def is_gm(self, player_id: str) -> bool:
        return self.gm_player_id is not None and player_id == self.gm_player_id

    def seat_of(self, player_id: str) -> str | None:
        return self.seat_assignments.get(player_id)

    def character_of(self, player_id: str) -> str | None:
        return self.player_id_to_character.get(player_id)

    def zone_of(self, character_id: str) -> str | None:
        return None  # Conservative: zones not yet tracked.

    def visible_to(self, viewer_character_id: str, target_character_id: str) -> bool:
        return False  # Conservative: unknown visibility stays masked.

    def owner_of_item(self, item_id: str) -> str | None:
        return None  # Conservative: ownership not yet tracked.

    def party_of(self, player_id: str) -> str | None:
        if player_id not in self.player_id_to_character:
            return None
        return self.party_id
