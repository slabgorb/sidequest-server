"""Turn management — phase tracking, round counting, and barrier semantics.

Two-tier turn model:
- interaction (granular): increments every player-narrator exchange.
  Powers fact/item discovery chronology. Monotonic, never resets.
- round (display): advances on meaningful narrative beats — location
  changes, chapter markers, trope escalations. Shown to the player.

ADR-006: Both counters always increment, never reset.
Persisted across sessions — loading a save restores exact counts.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TurnPhase(str, Enum):
    """The phases of a game turn (ADR-006)."""

    InputCollection = "InputCollection"
    IntentRouting = "IntentRouting"
    AgentExecution = "AgentExecution"
    StatePatch = "StatePatch"
    Broadcast = "Broadcast"


_PHASE_TRANSITIONS: dict[TurnPhase, TurnPhase] = {
    TurnPhase.InputCollection: TurnPhase.IntentRouting,
    TurnPhase.IntentRouting: TurnPhase.AgentExecution,
    TurnPhase.AgentExecution: TurnPhase.StatePatch,
    TurnPhase.StatePatch: TurnPhase.Broadcast,
    TurnPhase.Broadcast: TurnPhase.Broadcast,  # stays at last phase
}


class TurnManager(BaseModel):
    """Tracks current turn round, phase, and player input barrier.

    Two-tier model:
    - round: display counter for meaningful narrative beats.
    - interaction: monotonic counter for every player-narrator exchange.

    Both counters always increment, never reset. Persisted across sessions.

    The ``submitted`` set is runtime-only and skipped in serialization —
    populated as players submit and cleared on phase transitions.
    """

    model_config = {"extra": "forbid"}

    round: int = Field(default=1)
    interaction: int = Field(default=1)
    phase: TurnPhase = TurnPhase.InputCollection
    player_count: int = 1
    # submitted is runtime-only, not persisted.
    # We use a separate attribute, not a pydantic field, so it won't round-trip
    # through model_dump/validate_json.

    def model_post_init(self, __context: object) -> None:
        object.__setattr__(self, "_submitted", set())

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_round(self) -> int:
        return self.round

    def get_interaction(self) -> int:
        return self.interaction

    def get_phase(self) -> TurnPhase:
        return self.phase

    def set_player_count(self, count: int) -> None:
        self.player_count = count

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def submit_input(self, player_id: str) -> None:
        """Submit input for a player. Advances to IntentRouting when all players submitted."""
        if self.phase != TurnPhase.InputCollection:
            return
        submitted: set[str] = object.__getattribute__(self, "_submitted")
        submitted.add(player_id)
        if len(submitted) >= self.player_count:
            self.phase = TurnPhase.IntentRouting
            submitted.clear()

    def record_interaction(self) -> None:
        """Record a player-narrator interaction. Resets phase to InputCollection."""
        self.interaction += 1
        self.phase = TurnPhase.InputCollection
        submitted: set[str] = object.__getattribute__(self, "_submitted")
        submitted.clear()

    def advance_round(self) -> None:
        """Advance the display round (call on meaningful narrative beats)."""
        self.round += 1

    def advance(self) -> None:
        """Legacy: increment display round and reset phase."""
        self.round += 1
        self.phase = TurnPhase.InputCollection
        submitted: set[str] = object.__getattribute__(self, "_submitted")
        submitted.clear()

    def advance_phase(self) -> None:
        """Advance to the next phase within the current round."""
        self.phase = _PHASE_TRANSITIONS[self.phase]


class PreprocessedAction(BaseModel):
    """Player action after STT cleanup and perspective rewriting.

    Produced by the action preprocessor before being handed to agents.
    """

    model_config = {"frozen": True}

    you: str
    named: str
    intent: str
