"""Loud failure for the region-graph generator (CLAUDE.md: No Silent Fallbacks)."""

from __future__ import annotations


class ExpansionGenerationError(RuntimeError):
    """Raised when the re-roll loop cannot satisfy the Jaquays invariants."""

    def __init__(self, *, expansion_id: int, attempts: int, failing: list[str]) -> None:
        self.expansion_id = expansion_id
        self.attempts = attempts
        self.failing = list(failing)
        super().__init__(
            f"could not generate a Jaquays-valid expansion {expansion_id} "
            f"after {attempts} attempts; "
            f"last failing invariants: {', '.join(failing)}"
        )
