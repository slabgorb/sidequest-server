"""RED tests — Story 47-4 AC3 + AC6 (outcome span half).

Two new output handlers must be wired into magic Phase-5's OUTPUT_HANDLERS
dispatch:

* ``bond_strength_growth_via_intimacy`` — grows the bond ledger and emits
  ``rig.bond_event`` (and ``rig.voice_register_change`` if the chassis
  side crosses a tier).
* ``chassis_lineage_intimate`` — appends an ``intimate``-kind entry to
  the chassis lineage.

Plus: every confrontation outcome resolution must emit
``rig.confrontation_outcome`` (AC6).

The dispatch entry point is
``sidequest.magic.outputs.apply_mandatory_outputs(snapshot=..., outputs=[...],
actor=..., **context)``. Today it raises ``OutputUnknownError`` for both
new outputs — that is the RED state these tests pin.

The branch decision (clear_win vs refused) is a callsite concern; these
tests exercise the OUTPUT side of that decision, not the auto-fire
trigger (covered in test_galley_autofires_tea_brew.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.chassis import init_chassis_registry
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack
from sidequest.magic.outputs import apply_mandatory_outputs
from sidequest.telemetry.spans.rig import (
    SPAN_RIG_BOND_EVENT,
    SPAN_RIG_CONFRONTATION_OUTCOME,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _bootstrap_coyote_star_snapshot() -> GameSnapshot:
    """Boot a snapshot with Kestrel materialized, pre-bonded to trusted."""
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    from tests.integration.conftest import make_minimal_coyote_star_magic_state

    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Galley",
    )
    # S1 invariant: magic_state initialized before chassis registry.
    snap.magic_state = make_minimal_coyote_star_magic_state()
    init_chassis_registry(snap, pack)
    return snap


def _clear_win_outputs() -> list[str]:
    return ["bond_strength_growth_via_intimacy", "chassis_lineage_intimate"]


def _refused_outputs() -> list[str]:
    return ["chassis_lineage_intimate"]


def _common_ctx() -> dict:
    return {
        "chassis_id": "kestrel",
        "confrontation_id": "the_tea_brew",
        "register": "intimate",
        "branch": "clear_win",
        "turn_id": 10,
        "narrative_seed": "tea offered, accepted",
    }


@pytest.mark.integration
def test_clear_win_grows_bond_strength() -> None:
    """The bond_strength_growth_via_intimacy output must increase the
    chassis-side bond strength on Kestrel's bond_ledger entry for the
    actor."""
    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character

    ctx = _common_ctx()
    apply_mandatory_outputs(
        snapshot=snap,
        outputs=_clear_win_outputs(),
        actor="player_character",
        **ctx,
    )

    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after > bond_before, (
        f"bond did not grow: before={bond_before} after={bond_after}"
    )


@pytest.mark.integration
def test_clear_win_writes_intimate_lineage_entry() -> None:
    """chassis_lineage_intimate must append a lineage entry of kind=intimate."""
    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    lineage_before = len(kestrel.lineage)

    apply_mandatory_outputs(
        snapshot=snap,
        outputs=_clear_win_outputs(),
        actor="player_character",
        **_common_ctx(),
    )

    kestrel = snap.chassis_registry["kestrel"]
    assert len(kestrel.lineage) == lineage_before + 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_refused_writes_lineage_only_no_bond_change() -> None:
    """Refused branch records the encounter but bond strength is unchanged."""
    snap = _bootstrap_coyote_star_snapshot()
    kestrel = snap.chassis_registry["kestrel"]
    bond_before = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    lineage_before = len(kestrel.lineage)

    ctx = _common_ctx()
    ctx["branch"] = "refused"
    apply_mandatory_outputs(
        snapshot=snap,
        outputs=_refused_outputs(),
        actor="player_character",
        **ctx,
    )

    kestrel = snap.chassis_registry["kestrel"]
    bond_after = kestrel.bond_ledger[0].bond_strength_chassis_to_character
    assert bond_after == bond_before, (
        f"refused must not grow bond; before={bond_before} after={bond_after}"
    )
    assert len(kestrel.lineage) == lineage_before + 1
    assert kestrel.lineage[-1].kind == "intimate"


@pytest.mark.integration
def test_clear_win_emits_rig_bond_event_span(otel_capture) -> None:
    """rig.bond_event must fire when the bond ledger mutates (AC6)."""
    snap = _bootstrap_coyote_star_snapshot()

    apply_mandatory_outputs(
        snapshot=snap,
        outputs=_clear_win_outputs(),
        actor="player_character",
        **_common_ctx(),
    )

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert SPAN_RIG_BOND_EVENT in span_names, (
        f"rig.bond_event not emitted; saw {sorted(span_names)}"
    )


@pytest.mark.integration
def test_clear_win_emits_rig_confrontation_outcome_span(otel_capture) -> None:
    """rig.confrontation_outcome must fire on every outcome resolution (AC6)."""
    snap = _bootstrap_coyote_star_snapshot()

    apply_mandatory_outputs(
        snapshot=snap,
        outputs=_clear_win_outputs(),
        actor="player_character",
        **_common_ctx(),
    )

    span_names = {s.name for s in otel_capture.get_finished_spans()}
    assert SPAN_RIG_CONFRONTATION_OUTCOME in span_names, (
        f"rig.confrontation_outcome not emitted; saw {sorted(span_names)}"
    )
