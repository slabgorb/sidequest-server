"""ConfrontationDef.resolution_mode field tests (T1, dogfight port from Rust).

Covers ADR-077 Story 38-1: confrontations declare which resolution mode they
use (legacy beat selection vs. sealed-letter table lookup). Default must be
``ResolutionMode.beat_selection`` so existing genre packs continue to load
without any rules.yaml change.

Reference Rust test:
``sidequest-api/crates/sidequest-genre/tests/resolution_mode_story_38_1_tests.rs``.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models.pack import GenrePack
from sidequest.genre.models.rules import ConfrontationDef, ResolutionMode
from tests._helpers.genre_paths import GENRE_PACKS_DIR, find_pack_path

CONTENT_ROOT = GENRE_PACKS_DIR


def _has_real_content() -> bool:
    return CONTENT_ROOT.is_dir()


def load_pack(slug: str) -> GenrePack:
    return load_genre_pack(find_pack_path(slug))


def _conf_yaml(*, resolution_mode: str | None = None) -> str:
    """Minimal valid confrontation YAML, optionally with explicit resolution_mode."""
    rm_line = f"resolution_mode: {resolution_mode}\n" if resolution_mode is not None else ""
    return (
        "type: combat\n"
        "label: Test Combat\n"
        "category: combat\n"
        f"{rm_line}"
        "player_metric:\n"
        "  name: momentum\n"
        "  starting: 0\n"
        "  threshold: 10\n"
        "opponent_metric:\n"
        "  name: momentum\n"
        "  starting: 0\n"
        "  threshold: 10\n"
        "beats:\n"
        "  - id: attack\n"
        "    label: Attack\n"
        "    kind: strike\n"
        "    base: 2\n"
        "    stat_check: STR\n"
    )


# --- Enum -------------------------------------------------------------------


def test_resolution_mode_enum_has_all_variants():
    assert ResolutionMode.beat_selection.value == "beat_selection"
    assert ResolutionMode.sealed_letter_lookup.value == "sealed_letter_lookup"
    assert ResolutionMode.opposed_check.value == "opposed_check"


def test_resolution_mode_round_trips_through_yaml():
    """Every variant survives a YAML serialize→parse cycle on its string value."""
    for variant in (
        ResolutionMode.beat_selection,
        ResolutionMode.sealed_letter_lookup,
        ResolutionMode.opposed_check,
    ):
        dumped = yaml.safe_dump({"resolution_mode": variant.value})
        loaded = yaml.safe_load(dumped)
        assert ResolutionMode(loaded["resolution_mode"]) is variant


# --- ConfrontationDef.resolution_mode field --------------------------------


def test_resolution_mode_defaults_to_beat_selection_when_unspecified():
    """Existing packs that omit the field stay on legacy behavior."""
    cdef = ConfrontationDef.model_validate(yaml.safe_load(_conf_yaml()))
    assert cdef.resolution_mode is ResolutionMode.beat_selection


def test_resolution_mode_loads_sealed_letter_lookup_from_yaml():
    cdef = ConfrontationDef.model_validate(
        yaml.safe_load(_conf_yaml(resolution_mode="sealed_letter_lookup"))
    )
    assert cdef.resolution_mode is ResolutionMode.sealed_letter_lookup


def test_resolution_mode_loads_explicit_beat_selection_from_yaml():
    cdef = ConfrontationDef.model_validate(
        yaml.safe_load(_conf_yaml(resolution_mode="beat_selection"))
    )
    assert cdef.resolution_mode is ResolutionMode.beat_selection


def test_resolution_mode_unknown_value_rejected_loudly():
    """No silent fallback (CLAUDE.md): unknown mode must raise."""
    with pytest.raises(ValidationError):
        ConfrontationDef.model_validate(
            yaml.safe_load(_conf_yaml(resolution_mode="bogus_mode"))
        )


# --- Wiring tests: real genre packs still load -----------------------------


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
@pytest.mark.parametrize("slug", ["space_opera", "elemental_harmony", "heavy_metal"])
def test_existing_genre_pack_loads_with_resolution_mode_field(slug: str):
    """Regression / wiring: real packs load end-to-end and every confrontation
    exposes a typed ``ResolutionMode`` value (default or explicit)."""
    pack = load_pack(slug)
    assert pack.rules is not None
    assert pack.rules.confrontations, f"{slug} has no confrontations"
    for cdef in pack.rules.confrontations:
        assert isinstance(cdef.resolution_mode, ResolutionMode), (
            f"{slug} confrontation {cdef.confrontation_type!r} has non-enum "
            f"resolution_mode: {cdef.resolution_mode!r}"
        )


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
@pytest.mark.parametrize("slug", ["elemental_harmony", "heavy_metal"])
def test_non_combat_confrontations_default_to_beat_selection(slug: str):
    """Non-combat confrontations (negotiation, chase, parley) keep the
    legacy single-roll-vs-DC ``beat_selection`` mode. Only combat
    confrontations migrate to ``opposed_check`` (combat fairness, 2026-04-26).
    """
    pack = load_pack(slug)
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        if cdef.category == "combat":
            continue
        assert cdef.resolution_mode is ResolutionMode.beat_selection, (
            f"{slug} non-combat confrontation {cdef.confrontation_type!r} "
            f"unexpectedly set resolution_mode to {cdef.resolution_mode}"
        )


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_space_opera_dogfight_declares_sealed_letter_lookup():
    """Wiring: space_opera's dogfight confrontation opts into the new mode,
    proving the field is reachable from production load paths."""
    pack = load_pack("space_opera")
    assert pack.rules is not None
    dogfight = next(
        (c for c in pack.rules.confrontations if c.confrontation_type == "dogfight"),
        None,
    )
    assert dogfight is not None, "space_opera missing 'dogfight' confrontation"
    assert dogfight.resolution_mode is ResolutionMode.sealed_letter_lookup
