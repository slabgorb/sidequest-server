"""RED tests — Story 47-6 — tea ritual auto-fire never matches runtime inputs.

Story 47-4 shipped ``the_tea_brew`` and proved it fires *given the right
inputs*. Story 47-6 found that the runtime never produces those inputs:

* Bug 1: the narrator emits ``"The Kestrel — Galley"`` (chassis-qualified
  with em-dash). ``process_room_entry`` only matches ``"<chassis_id>:<room>"``
  or bare ``"galley"``. Em-dash form silently misses.
* Bug 2: ``init_chassis_registry`` seeds ``bond_ledger[].character_id =
  "player_character"`` (placeholder per chassis.py:251-253) and the
  rebind to the real character id was deferred and never landed. So
  ``chassis.bond_for(real_character_id)`` returns None even after
  chargen completes.

These tests pin both bug surfaces with the *literal* runtime inputs
captured in the 2026-05-03-coyote_star-3 playtest save:
- location string ``"The Kestrel — Galley"`` (em-dash)
- acting character name ``"Zanzibar Jones"`` (a real chargen name, not
  the placeholder)

Existing 47-4 tests use synthetic shapes (``"kestrel:galley"``, bare
``"Galley"``, ``"player_character"``) that paper over both bugs. The
test below feeding ``"The Kestrel — Galley"`` via the production
``_apply_narration_result_to_snapshot`` seam is the keystone regression
— if it passes, the playtest scenario works end-to-end.

Dev seam contract:
    sidequest.game.chassis.rebind_chassis_bonds_to_character(
        snapshot, character_id
    ) -> None
…rewrites every chassis_registry entry whose bond_ledger has a
``character_id == "player_character"`` placeholder to the real
character id. Idempotent (no-op if already rebound).
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


def _bootstrap_coyote_star_snapshot(*, location: str = "Cockpit") -> GameSnapshot:
    """Snapshot with chassis_registry materialized. Player starts at
    ``location`` (default Cockpit so transitions to Galley are real)."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location=location,
    )
    snap.turn_manager = TurnManager()
    snap.discovered_regions = []
    snap.quest_log = {}
    snap.lore_established = []
    snap.npc_registry = []
    snap.characters = []
    snap.encounter = None
    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    return snap


# ---------------------------------------------------------------------------
# AC1 + AC5: room-name matcher accepts the chassis-qualified em-dash form.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_process_room_entry_resolves_em_dash_chassis_qualified_form() -> None:
    """Narrator-emitted ``"The Kestrel — Galley"`` (em-dash, chassis name
    prefix) must resolve to ``kestrel:galley`` and fire the_tea_brew.

    This is the literal string captured from server log:
        state.location_update old='The Kestrel — Cockpit'
                              new='The Kestrel — Galley'
    """
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    process_room_entry(
        snap,
        character_id="player_character",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )

    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "the_tea_brew did not fire when room_id was the narrator's literal "
        f"em-dash form 'The Kestrel — Galley': bond stayed at {bond_before}"
    )
    assert len(kestrel.lineage) == 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_process_room_entry_em_dash_form_stamps_cooldown() -> None:
    """Cooldown ledger key must use the chassis_id (`kestrel`), not the
    qualified name. Otherwise a re-entry test would not see the cooldown."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(
        snap,
        character_id="player_character",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )
    assert "kestrel:the_tea_brew" in snap.chassis_autofire_cooldowns, (
        f"cooldown ledger missing 'kestrel:the_tea_brew' key; saw "
        f"{list(snap.chassis_autofire_cooldowns)}"
    )
    assert snap.chassis_autofire_cooldowns["kestrel:the_tea_brew"] == 10


@pytest.mark.integration
def test_narration_apply_em_dash_location_fires_tea_brew() -> None:
    """Keystone E2E regression: the playtest's exact narrator output flows
    through ``_apply_narration_result_to_snapshot`` and fires the_tea_brew.

    This is the test the 2026-05-03-coyote_star-3 save would have caught —
    every other failure mode chains off it."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap = _bootstrap_coyote_star_snapshot(location="The Kestrel — Cockpit")
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    result = NarrationTurnResult(
        narration="You walk into the galley. Kanga is at the kettle.",
        location="The Kestrel — Galley",
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Keith",
        room=room_for(snap, slug="coyote_star"),
        acting_character_name="player_character",
    )

    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "narration_apply did not fire the_tea_brew when narrator emitted "
        "'The Kestrel — Galley'. The em-dash form silently misses the "
        "chassis.interior_rooms membership check in process_room_entry."
    )
    assert len(kestrel.lineage) == lineage_before + 1


# ---------------------------------------------------------------------------
# AC3: bond-ledger rebind from "player_character" placeholder to real id.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rebind_replaces_placeholder_character_id() -> None:
    """``rebind_chassis_bonds_to_character`` must replace the placeholder
    ``"player_character"`` in every chassis bond_ledger with the real
    character id from chargen."""
    from sidequest.game.chassis import rebind_chassis_bonds_to_character

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    # init_chassis_registry must seed the placeholder so the rebind has work.
    assert kestrel.bond_ledger[0].character_id == "player_character", (
        "fixture invariant violated: expected placeholder seed before rebind"
    )

    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")

    kestrel = snap.chassis_registry["kestrel"]
    assert kestrel.bond_ledger[0].character_id == "Zanzibar Jones", (
        f"rebind did not rewrite placeholder; saw {kestrel.bond_ledger[0].character_id!r}"
    )


@pytest.mark.integration
def test_rebind_is_idempotent() -> None:
    """Running rebind twice with the same character must not duplicate or
    corrupt entries — chargen.complete may fire more than once in MP."""
    from sidequest.game.chassis import rebind_chassis_bonds_to_character

    snap = _bootstrap_coyote_star_snapshot()
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")  # no-op

    kestrel = snap.chassis_registry["kestrel"]
    assert len(kestrel.bond_ledger) == 1
    assert kestrel.bond_ledger[0].character_id == "Zanzibar Jones"


@pytest.mark.integration
def test_rebind_leaves_already_rebound_entries_alone() -> None:
    """If a bond_ledger already has the real character_id (e.g. a save
    rehydrating with a rebind already applied), the rebind must be a
    no-op — never overwrite a real id with a different real id."""
    from sidequest.game.chassis import rebind_chassis_bonds_to_character

    snap = _bootstrap_coyote_star_snapshot()
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")

    # A second character (MP scenario in future). Today this should still
    # leave Zanzibar's existing real-id entry untouched — only entries
    # still keyed against the placeholder may be rewritten.
    rebind_chassis_bonds_to_character(snap, "Other Character")

    kestrel = snap.chassis_registry["kestrel"]
    assert kestrel.bond_ledger[0].character_id == "Zanzibar Jones"


@pytest.mark.integration
def test_rebound_bond_makes_real_character_eligible_for_tea_brew() -> None:
    """After rebind, ``process_room_entry(character_id="Zanzibar Jones",
    room_id="The Kestrel — Galley")`` must fire the_tea_brew. Today
    fails twice — once on the matcher, once on the bond lookup."""
    from sidequest.game.chassis import rebind_chassis_bonds_to_character
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    process_room_entry(
        snap,
        character_id="Zanzibar Jones",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )

    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "Layered failure: even with em-dash matcher *and* bond rebind, "
        f"the_tea_brew did not fire. bond {bond_before} → {bond_after}."
    )


@pytest.mark.integration
def test_narration_apply_with_real_character_and_em_dash_location() -> None:
    """Full playtest reproduction: narrator emits 'The Kestrel — Galley',
    acting character is 'Zanzibar Jones' (real chargen name), bond was
    rebound at chargen-complete. Asserts the_tea_brew fires.

    This is what the 2026-05-03-coyote_star-3 save SHOULD have produced
    and didn't. If this test passes, the playtest bug is closed."""
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.game.chassis import rebind_chassis_bonds_to_character
    from sidequest.server.narration_apply import (
        _apply_narration_result_to_snapshot,
    )

    snap = _bootstrap_coyote_star_snapshot(location="The Kestrel — Cockpit")
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    result = NarrationTurnResult(
        narration="You head down the corridor and into the galley.",
        location="The Kestrel — Galley",
    )
    _apply_narration_result_to_snapshot(
        snap,
        result,
        player_name="Keith",
        room=room_for(snap, slug="coyote_star"),
        acting_character_name="Zanzibar Jones",
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "Playtest reproduction failed: narrator's literal 'The Kestrel — "
        "Galley' + real character id 'Zanzibar Jones' did not fire "
        "the_tea_brew end-to-end."
    )
    assert len(kestrel.lineage) == 1
    assert kestrel.lineage[-1].kind == "intimate"
