"""RED tests — Story 47-6 AC2 — opening pipeline triggers room-entry hook.

Bug 3: ``sidequest/server/dispatch/opening.py`` builds an opening
directive with ``interior_room_label`` but does NOT route through
``process_room_entry``. So the FIRST eligible moment — turn 1, cold
start in galley with bond ``trusted`` — silently skips eligibility
evaluation. Only narrator-driven location *updates* (e.g., turn 22's
cockpit→galley) reach the hook.

In the 2026-05-03-coyote_star-3 playtest, the player started in the
galley (turns 1-5) with bond_tier_chassis=trusted. ``the_tea_brew``
should have fired on turn 1. It did not, because the opening pipeline
never asked.

The contract pinned here: after a session opens the player into an
interior_room with eligible bond, ``the_tea_brew`` is evaluated. The
test asserts the side effect (bond grew, lineage entry exists) rather
than naming a specific seam — Dev may add a session-start hook, may
extend ``init_chassis_registry``, may patch the opening dispatch
itself. The contract is "if you start the session in the galley with
the right bond, the ritual fires."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.chassis import init_chassis_registry
from sidequest.game.session import GameSnapshot
from sidequest.game.turn import TurnManager
from sidequest.genre.loader import load_genre_pack

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _bootstrap_session_opened_in_galley() -> GameSnapshot:
    """Snapshot in the same shape a fresh session takes when the opening
    places the player in the galley — chassis registered and location
    set to the chassis-qualified galley."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="The Kestrel — Galley",
    )
    snap.turn_manager = TurnManager()
    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    return snap


@pytest.mark.integration
def test_session_opened_in_galley_evaluates_tea_brew() -> None:
    """A session that opens with location='The Kestrel — Galley' and
    bond_tier_chassis=trusted must evaluate the_tea_brew at session-start
    time, not wait for a later location transition.

    Dev contract: a function such as
    ``sidequest.game.room_movement.process_session_open(snapshot,
    character_id, current_turn)`` (or equivalent seam wired into the
    opening pipeline) must run room-entry eligibility against
    ``snapshot.location`` when the opening completes. The test imports
    a candidate seam name; rename if Dev picks a different one — the
    contract is the side effect, not the import path."""
    from sidequest.game.room_movement import process_session_open

    snap = _bootstrap_session_opened_in_galley()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    process_session_open(
        snap,
        character_id="player_character",
        current_turn=1,
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "the_tea_brew did not fire when the session opened with the "
        f"player already in the galley. bond stayed at {bond_before}. "
        "The opening pipeline must trigger room-entry eligibility for "
        "the starting interior_room — not just narrator-driven "
        "location *changes*."
    )
    assert len(kestrel.lineage) == lineage_before + 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_session_opened_outside_galley_does_not_fire_tea_brew() -> None:
    """Counter-test: a session opening into Cockpit must NOT fire
    the_tea_brew. Guards against a wiring that fires unconditionally
    on session open."""
    from sidequest.game.room_movement import process_session_open

    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="The Kestrel — Cockpit",
    )
    snap.turn_manager = TurnManager()
    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    process_session_open(
        snap,
        character_id="player_character",
        current_turn=1,
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after == bond_before
    assert len(kestrel.lineage) == 0


@pytest.mark.integration
def test_session_opened_in_galley_with_real_character_fires_tea_brew() -> None:
    """End-to-end opening reproduction: rebind bond_seed to a real
    character id, then run process_session_open. This is the path the
    real chargen-complete handler should take. If this fails, the
    tea_brew is broken at the very first turn for any non-placeholder
    character — i.e., every real playthrough."""
    from sidequest.game.chassis import rebind_chassis_bonds_to_character
    from sidequest.game.room_movement import process_session_open

    snap = _bootstrap_session_opened_in_galley()
    rebind_chassis_bonds_to_character(snap, "Zanzibar Jones")

    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    process_session_open(
        snap,
        character_id="Zanzibar Jones",
        current_turn=1,
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        "Real-character session-open into the galley did not fire "
        "the_tea_brew. This was the playtest's actual failure mode."
    )
