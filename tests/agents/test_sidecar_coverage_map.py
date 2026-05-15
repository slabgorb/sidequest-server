"""Sidecar coverage map — enforces Phase C definition-of-done.

Each row maps a former sidecar field to its successor tool. Phase D
cannot proceed until every row has a successor.
"""

from __future__ import annotations

import pytest

# Each entry: sidecar_field -> successor tool name (or None if not yet migrated).
COVERAGE_MAP: dict[str, str | None] = {
    "dice_roll": "roll_dice",
    "patches_hp": "apply_damage",
    "patches_status": "apply_status",
    "patches_resource_pool": "update_resource_pool",
    "patches_disposition": "update_npc_disposition",
    "patches_other": None,
    "journal_entries": "commit_known_fact",
    "scenario_advances": None,
    "encounter_advances": None,
    "magic_effects": None,
    "trope_tick": None,
    "confrontation_advances": None,
}


@pytest.mark.xfail(
    reason="Phase D gate — flips to passing after Task 27 (apply_world_patch) "
    "ticks the final coverage map entry.",
    strict=True,
)
def test_phase_c_complete() -> None:
    """Phase D gate — must pass before deleting the sidecar parser."""
    unmigrated = [k for k, v in COVERAGE_MAP.items() if v is None]
    assert not unmigrated, (
        f"Sidecar fields without tool successors: {unmigrated!r}. Phase D cannot proceed."
    )
