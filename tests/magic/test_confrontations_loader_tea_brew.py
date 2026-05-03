"""RED tests — Story 47-4 AC1.

`the_tea_brew` is the first intimate-register confrontation. The Phase-5
loader must accept it with three new schema concepts:

* ``register: intimate`` — confrontation register tag
* ``rig_tie_ins: [voidborn_freighter]`` — chassis-class binding
* ``fire_conditions`` — room/bond/cooldown gates (separate from the
  bar-DSL ``auto_fire_trigger`` Phase 5 already supports)

These tests load the world's actual confrontations.yaml from the
sidequest-content subrepo. They will fail until both the YAML appends
``the_tea_brew`` and ``ConfrontationDefinition`` accepts the new fields.

Cross-cuts AC1 ("loads through magic Phase 5 confrontations loader for
coyote_star") and pins the YAML shape the rest of Phase C depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.magic.confrontations import (
    ConfrontationDefinition,
    load_confrontations,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
COYOTE_STAR_CONFRONTATIONS_YAML = (
    REPO_ROOT
    / "sidequest-content"
    / "genre_packs"
    / "space_opera"
    / "worlds"
    / "coyote_star"
    / "confrontations.yaml"
)


def _load_or_skip() -> list[ConfrontationDefinition]:
    if not COYOTE_STAR_CONFRONTATIONS_YAML.exists():
        pytest.skip("coyote_star confrontations.yaml not present")
    return load_confrontations(COYOTE_STAR_CONFRONTATIONS_YAML)


def _by_id(confs: list[ConfrontationDefinition], cid: str) -> ConfrontationDefinition:
    match = next((c for c in confs if c.id == cid), None)
    if match is None:
        pytest.fail(f"confrontation {cid!r} not found in loaded list")
    return match


def test_tea_brew_present_with_intimate_register() -> None:
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")
    assert getattr(tea_brew, "register", None) == "intimate"


def test_tea_brew_rig_tie_ins_voidborn_freighter() -> None:
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")
    assert getattr(tea_brew, "rig_tie_ins", None) == ["voidborn_freighter"]


def test_tea_brew_fire_conditions_parsed() -> None:
    """fire_conditions block is the new room+bond+cooldown gate.

    Distinct from auto_fire_trigger (the bar-DSL Phase 5 uses for
    sanity/notice thresholds). the_tea_brew opts into auto_fire but
    routes through fire_conditions instead.
    """
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")

    fc = getattr(tea_brew, "fire_conditions", None)
    assert fc is not None, "fire_conditions block missing on the_tea_brew"

    interior_room = getattr(fc, "interior_room_present", None)
    bond_tier_min = getattr(fc, "bond_tier_min", None)
    cooldown_turns = getattr(fc, "cooldown_turns", None)

    assert interior_room == "galley"
    assert bond_tier_min == "familiar"
    assert cooldown_turns == 6


def test_tea_brew_clear_win_outputs() -> None:
    """clear_win drives both bond growth AND lineage trace."""
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")
    outputs = tea_brew.outcomes["clear_win"].mandatory_outputs
    assert "bond_strength_growth_via_intimacy" in outputs
    assert "chassis_lineage_intimate" in outputs


def test_tea_brew_refused_outputs_lineage_only() -> None:
    """Refused records the encounter happened but does NOT grow bond."""
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")
    outputs = tea_brew.outcomes["refused"].mandatory_outputs
    assert outputs == ["chassis_lineage_intimate"], (
        f"refused must be lineage-only; got {outputs!r}"
    )


def test_tea_brew_auto_fire_true() -> None:
    confs = _load_or_skip()
    tea_brew = _by_id(confs, "the_tea_brew")
    assert tea_brew.auto_fire is True
