"""RED test — Story 47-4 rework (Reviewer round 1 finding).

The original 47-4 GREEN shipped `process_room_entry` as a function that
no production caller invokes. Eighteen tests proved the post-hook chain
works end-to-end FROM the function — but the narrator's runtime location
mutation site at ``sidequest/server/narration_apply.py`` does NOT call
it. Tonight's playtest would walk Kestrel into the Galley and see
absolutely nothing fire.

This test pins the production wiring: when the narrator's
``NarrationTurnResult.location`` mutates `snap.location` through
``_apply_narration_result_to_snapshot``, the rig auto-fire pipeline
must trigger via ``process_room_entry`` (or whichever Dev-chosen seam).
We assert the SIDE EFFECT (bond grew on Kestrel) rather than naming
``process_room_entry`` directly so Dev has flexibility on the wiring
shape — the contract is "narrator says 'you go to the Galley',
the_tea_brew fires."

Also pins the bare-room-name → chassis-room resolution implicitly:
``result.location`` is a world-name string like ``"Galley"`` (NOT
``"kestrel:galley"``), so the production path must either translate
the bare name to a chassis-scoped id OR teach process_room_entry to
resolve bare names against `chassis.interior_rooms`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.chassis import init_chassis_registry
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.genre.loader import load_genre_pack
from tests._helpers.session_room import room_for

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _bootstrap_coyote_star_snapshot() -> GameSnapshot:
    """Snapshot with chassis_registry, world_confrontations, ready for the
    apply path. Player starts NOT in the Galley so the location mutation
    is real."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Cockpit",
    )
    snap.turn_manager = TurnManager()
    snap.discovered_regions = []
    snap.quest_log = {}
    snap.lore_established = []
    snap.npc_registry = []
    snap.characters = []
    snap.encounter = None
    # S1 invariant (2026-05-04): magic_state initialized before chassis.
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    return snap


@pytest.mark.integration
def test_narration_apply_galley_location_fires_tea_brew() -> None:
    """The production narration_apply seam must wire the rig auto-fire
    pipeline. A NarrationTurnResult with location='Galley' must cause
    bond to grow on Kestrel via the_tea_brew clear_win path."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    result = NarrationTurnResult(
        narration="You drift into the Galley. The kettle is already warm.",
        location="Galley",
    )

    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Zee",
        room=room_for(snap, slug="coyote_star"),
        acting_character_name="player_character",
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        f"narration_apply did not trigger the rig auto-fire pipeline: "
        f"bond before={bond_before} after={bond_after}. "
        "process_room_entry (or equivalent seam) is not wired into the "
        "location-mutation site at narration_apply.py:~941."
    )
    assert len(kestrel.lineage) == lineage_before + 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_narration_apply_non_chassis_location_is_silent_no_op() -> None:
    """A location mutation to a bare world-name with no matching chassis
    interior room must NOT fire any rig confrontation. Guards against
    a too-eager wiring that fires on every location change."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    result = NarrationTurnResult(
        narration="You arrive at the docking ring office. It smells of stale recycled air.",
        location="Docking Ring Office",
    )

    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Zee",
        room=room_for(snap, slug="coyote_star"),
        acting_character_name="player_character",
    )

    kestrel = snap.chassis_registry["kestrel"]
    assert kestrel.bond_ledger[0].bond_strength_chassis_to_character == bond_before
    assert len(kestrel.lineage) == lineage_before
