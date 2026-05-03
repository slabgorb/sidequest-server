"""Loader for worlds/<w>/confrontations.yaml — Story 47-3 Task 5.2.

Tests the YAML loader that produces ConfrontationDefinition objects from
``sidequest-content/genre_packs/<genre>/worlds/<world>/confrontations.yaml``.

Per design Decision #8 (plan 2026-04-28-magic-system-coyote-reach-v1.md):
every outcome branch (clear_win, pyrrhic_win, clear_loss, refused) must
declare at least one mandatory_output. Missing branches and missing files
fail loud — no silent fallback (CLAUDE.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.magic.confrontations import (
    ConfrontationDefinition,
    ConfrontationLoaderError,
    load_confrontations,
)


@pytest.fixture
def coyote_yaml_path(tmp_path: Path) -> Path:
    yaml_text = """
confrontations:
  - id: the_salvage
    label: "The Salvage"
    plugin_tie_ins: [item_legacy_v1, innate_v1]
    auto_fire: false
    rounds: 2
    resource_pool:
      primary: sanity
      secondary: bond
    description: "Discovery confrontation."
    outcomes:
      clear_win:
        mandatory_outputs: [item_acquired]
      pyrrhic_win:
        mandatory_outputs: [item_acquired, sanity_decrement]
      clear_loss:
        mandatory_outputs: [sanity_decrement, status_add_wound]
      refused:
        mandatory_outputs: [item_acquired_with_low_bond]
  - id: the_bleeding_through
    label: "The Bleeding-Through"
    plugin_tie_ins: [innate_v1]
    auto_fire: true
    auto_fire_trigger: "sanity <= 0.40"
    rounds: 1
    resource_pool:
      primary: sanity
      secondary: vitality
    description: "Cannot suppress what they pick up."
    outcomes:
      clear_win:
        mandatory_outputs: [control_tier_advance]
      pyrrhic_win:
        mandatory_outputs: [control_tier_advance, status_add_scar]
      clear_loss:
        mandatory_outputs: [status_add_scar]
      refused:
        mandatory_outputs: [sanity_decrement]
"""
    p = tmp_path / "confrontations.yaml"
    p.write_text(yaml_text)
    return p


def test_loader_returns_list_of_definitions(coyote_yaml_path: Path) -> None:
    confs = load_confrontations(coyote_yaml_path)
    assert len(confs) == 2
    assert all(isinstance(c, ConfrontationDefinition) for c in confs)


def test_definition_fields_populated(coyote_yaml_path: Path) -> None:
    confs = load_confrontations(coyote_yaml_path)
    salvage = next(c for c in confs if c.id == "the_salvage")
    assert salvage.label == "The Salvage"
    assert salvage.plugin_tie_ins == ["item_legacy_v1", "innate_v1"]
    assert salvage.auto_fire is False
    assert salvage.rounds == 2
    assert salvage.resource_pool["primary"] == "sanity"
    assert salvage.resource_pool["secondary"] == "bond"


def test_auto_fire_trigger_loaded(coyote_yaml_path: Path) -> None:
    confs = load_confrontations(coyote_yaml_path)
    bt = next(c for c in confs if c.id == "the_bleeding_through")
    assert bt.auto_fire is True
    assert bt.auto_fire_trigger == "sanity <= 0.40"


def test_all_four_branches_required(coyote_yaml_path: Path) -> None:
    """Every confrontation must declare all four outcome branches."""
    confs = load_confrontations(coyote_yaml_path)
    for c in confs:
        assert set(c.outcomes.keys()) == {
            "clear_win",
            "pyrrhic_win",
            "clear_loss",
            "refused",
        }, f"{c.id} missing branches: {set(c.outcomes.keys())}"


def test_mandatory_outputs_required_per_branch(coyote_yaml_path: Path) -> None:
    """Decision #8: every branch must have ≥1 mandatory_output."""
    confs = load_confrontations(coyote_yaml_path)
    for c in confs:
        for branch_name, branch in c.outcomes.items():
            assert len(branch.mandatory_outputs) >= 1, (
                f"{c.id} branch {branch_name} has no mandatory_outputs"
            )


def test_missing_branch_fails_loud(tmp_path: Path) -> None:
    """No silent fallback: missing branches raise ConfrontationLoaderError."""
    yaml_text = """
confrontations:
  - id: bad
    label: "Bad"
    plugin_tie_ins: [innate_v1]
    auto_fire: false
    rounds: 1
    resource_pool: {primary: sanity}
    description: "x"
    outcomes:
      clear_win:
        mandatory_outputs: [x]
      # missing pyrrhic_win, clear_loss, refused
"""
    p = tmp_path / "bad.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ConfrontationLoaderError, match="branch"):
        load_confrontations(p)


def test_empty_mandatory_outputs_fails_loud(tmp_path: Path) -> None:
    """Branch with empty mandatory_outputs is a Decision #8 violation."""
    yaml_text = """
confrontations:
  - id: bad
    label: "Bad"
    plugin_tie_ins: []
    auto_fire: false
    rounds: 1
    resource_pool: {primary: sanity}
    description: "x"
    outcomes:
      clear_win: {mandatory_outputs: []}
      pyrrhic_win: {mandatory_outputs: [x]}
      clear_loss: {mandatory_outputs: [x]}
      refused: {mandatory_outputs: [x]}
"""
    p = tmp_path / "empty.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ConfrontationLoaderError):
        load_confrontations(p)


def test_missing_file_fails_loud() -> None:
    with pytest.raises(ConfrontationLoaderError, match="not found"):
        load_confrontations(Path("/nonexistent/confrontations.yaml"))


def test_malformed_yaml_fails_loud(tmp_path: Path) -> None:
    """Lang-review #1: silent exception swallowing forbidden — yaml errors propagate."""
    p = tmp_path / "broken.yaml"
    p.write_text("confrontations:\n  - id: x\n    label: [unbalanced\n")
    with pytest.raises(ConfrontationLoaderError):
        load_confrontations(p)


def test_loads_real_coyote_star_yaml() -> None:
    """Wiring test: the production Coyote Star confrontations.yaml loads.

    The five magic-Phase-5 named confrontations plus the rig-Phase-C
    intimate ``the_tea_brew`` (Story 47-4) must parse from the real
    genre-pack file. This is the wire-first signal — the loader must
    accept the actual content authored at
    ``sidequest-content/genre_packs/space_opera/worlds/coyote_star/confrontations.yaml``.
    """
    repo_root = Path(__file__).resolve().parents[3]
    yaml_path = (
        repo_root
        / "sidequest-content"
        / "genre_packs"
        / "space_opera"
        / "worlds"
        / "coyote_star"
        / "confrontations.yaml"
    )
    assert yaml_path.exists(), f"production yaml missing: {yaml_path}"
    confs = load_confrontations(yaml_path)
    ids = {c.id for c in confs}
    assert ids == {
        "the_standoff",
        "the_salvage",
        "the_bleeding_through",
        "the_quiet_word",
        "the_long_resident",
        "the_tea_brew",
    }, f"expected the six named confrontations, got {ids}"
