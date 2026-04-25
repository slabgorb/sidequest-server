"""TurnRecord — immutable snapshot of a completed dispatch turn.

Assembled at the end of session_handler._execute_narration_turn and put
on the validator queue. Frozen for immutability across the queue boundary.

Per ADR-089 §2.1 (deliberate departure from Rust ADR-031), Python stores
snapshot_before_hash + snapshot_after + delta rather than two full
GameSnapshot clones — same validation power without the double-clone
cost on every turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PatchSummary:
    """Compact record of one patch applied during a turn."""

    patch_type: str  # "world" | "combat" | "chase" | "scenario"
    fields_changed: list[str]


@dataclass(frozen=True)
class TurnRecord:
    """One completed turn, ready for narrative validation."""

    turn_id: int
    timestamp: datetime
    player_id: str
    player_input: str
    classified_intent: str
    agent_name: str
    narration: str
    patches_applied: list[PatchSummary]
    snapshot_before_hash: str
    snapshot_after: Any  # GameSnapshot — typed Any to keep telemetry game-layer-free
    delta: Any  # StateDelta — same reason
    beats_fired: list[tuple[str, float]]  # (trope_name, threshold)
    extraction_tier: int
    token_count_in: int
    token_count_out: int
    agent_duration_ms: int
    is_degraded: bool
