"""Per-encounter taunt state.

Tracks which actor is currently 'taunting' (forcing enemy attention onto
themselves), how many rounds remain on the effect, and how many damage
redirects have already fired this round (capped at 1 per spec §8).

Spec: docs/superpowers/specs/2026-05-10-class-mechanical-surface-design.md §8.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TauntState:
    """Mutable per-encounter taunt tracker.

    Attributes:
        active_actor: Actor ID currently taunting, or None when no taunt is active.
        remaining_rounds: Rounds left on the current taunt; decays to 0 at end of round.
        redirects_this_round: Number of damage redirects fired this round (cap: 1 per spec §8).
    """

    active_actor: str | None = None
    remaining_rounds: int = 0
    redirects_this_round: int = 0

    def activate(self, actor_id: str) -> None:
        """Start a 1-round taunt. Resets redirect counter."""
        self.active_actor = actor_id
        self.remaining_rounds = 1
        self.redirects_this_round = 0

    def end_of_round_decay(self) -> None:
        """Decrement; at 0, clear the actor and redirect counter."""
        if self.remaining_rounds > 0:
            self.remaining_rounds -= 1
        if self.remaining_rounds == 0:
            self.active_actor = None
            self.redirects_this_round = 0

    def try_consume_redirect(self) -> bool:
        """Returns True if a redirect is available this round and consumes it.
        Returns False if cap is reached or no taunt is active."""
        if self.active_actor is None:
            return False
        if self.redirects_this_round >= 1:
            return False
        self.redirects_this_round += 1
        return True
