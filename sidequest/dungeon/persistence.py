"""Beneath Sünden Plan 5 — dungeon persistence layer.

Persists the contiguous region graph, frontier, mutation overlay, and
complication ledger into the existing per-session SQLite save DB. The
store operates on a CALLER-SUPPLIED connection (never opens its own) so
Plan 7's materializer can wrap game-save + dungeon-save in one
transaction (spec §7.5). No materializer/session caller exists yet —
honest deferral, Plan 2-4 precedent.
"""

from __future__ import annotations


class DungeonStore:
    """Defined in Task 4."""
