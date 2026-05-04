"""RED tests — Story 47-4 AC2 + AC5 + AC6 (fire-side span half).

When the player enters Kestrel's Galley AND the chassis-side bond is at
or above ``familiar`` AND the cooldown has elapsed, ``the_tea_brew``
must auto-fire through the magic Phase-5 outcome pipeline.

These are the hardest tests in the slice because they cross three
seams the plan calls out as "discover and adapt" rather than "use
existing":

* The room-entry hook (``room_movement.py`` does not yet host one — the
  whole file is just chassis-room init today).
* The room+bond+cooldown eligibility evaluator (Phase 5 only ships a
  bar-DSL evaluator).
* The cooldown ledger (Phase 5 doesn't ship one).

The Dev seam expected here is::

    sidequest.game.room_movement.process_room_entry(
        snapshot, *, character_id, room_id, current_turn
    )

If Dev picks a different name, retarget the import — the contract is
unchanged: post-room-entry call that runs eligibility, dispatches the
outcome, stamps the cooldown, and emits OTEL spans.

This is the GM-flagged risk surface: if these tests pass, the actual
auto-fire pipeline works end-to-end. If they pass with mocks but fail
in playtest, the wiring tests in ``test_kestrel_chassis_registry.py``
are coverage-light somewhere upstream.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.chassis import init_chassis_registry
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack
from sidequest.telemetry.spans.rig import (
    SPAN_RIG_BOND_EVENT,
    SPAN_RIG_CONFRONTATION_OUTCOME,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _bootstrap_coyote_star_snapshot() -> GameSnapshot:
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Cockpit",  # start NOT in Galley
    )
    # S1 invariant (2026-05-04): magic_state must be initialized before
    # init_chassis_registry. The chassis loader writes confrontations
    # directly into snapshot.magic_state.confrontations now.
    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    return snap


def _galley_room_id() -> str:
    """Kestrel's Galley room id — chassis-prefixed per ship-map convention.

    Phase A's interior content ships rooms as ``cockpit``, ``galley``,
    ``engineering``, ``cargo`` on ``voidborn_freighter``. The autofire
    eligibility check operates on chassis-scoped room ids.
    """
    return "kestrel:galley"


def _entry_kwargs(turn: int) -> dict:
    return {
        "character_id": "player_character",
        "room_id": _galley_room_id(),
        "current_turn": turn,
    }


@pytest.mark.integration
def test_galley_entry_with_eligible_bond_fires_tea_brew() -> None:
    """E2E AC5: Kestrel's pre-load bond is trusted (0.45 ≥ familiar=0.30
    on the standard tier ladder), so the first Galley entry must fire
    the_tea_brew clear_win — bond grows."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    process_room_entry(snap, **_entry_kwargs(turn=10))

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        f"the_tea_brew did not fire on Galley entry: "
        f"bond before={bond_before} after={bond_after}"
    )


@pytest.mark.integration
def test_galley_entry_writes_intimate_lineage() -> None:
    """E2E AC5: lineage entry written on auto-fire clear_win."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    lineage_before = len(kestrel.lineage)

    process_room_entry(snap, **_entry_kwargs(turn=10))

    kestrel = snap.chassis_registry["kestrel"]
    assert len(kestrel.lineage) == lineage_before + 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_non_galley_room_does_not_fire_tea_brew() -> None:
    """fire_conditions.interior_room_present=galley must gate firing.

    Entering Cockpit with the same eligible bond must NOT fire
    the_tea_brew (no bond change, no lineage entry)."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    process_room_entry(
        snap,
        character_id="player_character",
        room_id="kestrel:cockpit",
        current_turn=10,
    )

    kestrel = snap.chassis_registry["kestrel"]
    assert (
        kestrel.bond_ledger[0].bond_strength_chassis_to_character == bond_before
    )
    assert len(kestrel.lineage) == lineage_before


@pytest.mark.integration
def test_galley_re_entry_within_cooldown_does_not_refire() -> None:
    """fire_conditions.cooldown_turns=6 must suppress a second fire if the
    player re-enters Galley before 6 turns elapse."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(snap, **_entry_kwargs(turn=10))

    kestrel = snap.chassis_registry["kestrel"]
    bond_after_first = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_after_first = len(kestrel.lineage)

    # Re-enter Galley three turns later — cooldown has not elapsed.
    process_room_entry(snap, **_entry_kwargs(turn=13))

    kestrel = snap.chassis_registry["kestrel"]
    bond_after_second = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after_second == bond_after_first, (
        f"cooldown did not hold: bond moved from {bond_after_first} "
        f"to {bond_after_second} on re-entry within cooldown"
    )
    assert len(kestrel.lineage) == lineage_after_first


@pytest.mark.integration
def test_galley_re_entry_after_cooldown_refires() -> None:
    """After cooldown_turns=6 elapses, re-entry must fire again."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(snap, **_entry_kwargs(turn=10))
    bond_after_first = (
        snap.chassis_registry["kestrel"].bond_ledger[0].bond_strength_chassis_to_character
    )

    # Re-enter at turn 17 — 7 turns after the first fire (>6).
    process_room_entry(snap, **_entry_kwargs(turn=17))

    kestrel = snap.chassis_registry["kestrel"]
    bond_after_second = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after_second > bond_after_first, (
        f"second fire did not grow bond: {bond_after_first} → {bond_after_second}"
    )


@pytest.mark.integration
def test_galley_entry_emits_rig_outcome_span(otel_capture) -> None:
    """AC6: rig.confrontation_outcome must fire on auto-fire resolution."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(snap, **_entry_kwargs(turn=10))

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert SPAN_RIG_CONFRONTATION_OUTCOME in span_names, (
        f"rig.confrontation_outcome not emitted on auto-fire; "
        f"saw {sorted(span_names)}"
    )


@pytest.mark.integration
def test_galley_entry_emits_rig_bond_event_span(otel_capture) -> None:
    """AC6: rig.bond_event must fire because bond mutated."""
    from sidequest.game.room_movement import process_room_entry

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(snap, **_entry_kwargs(turn=10))

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert SPAN_RIG_BOND_EVENT in span_names, (
        f"rig.bond_event not emitted on auto-fire bond mutation; "
        f"saw {sorted(span_names)}"
    )
