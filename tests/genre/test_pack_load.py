"""Integration test: caverns_and_claudes pack loads under dual-dial momentum schema.

Verifies that the migrated rules.yaml round-trips through GenrePack validation with
the dual-dial ConfrontationDef schema (player_metric + opponent_metric, BeatDef.kind).

See Task 27 — canary migration for dual-track momentum Phase 3.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models.pack import GenrePack

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"
CC_PACK_DIR = CONTENT_ROOT / "caverns_and_claudes"


def _has_real_content() -> bool:
    return CC_PACK_DIR.is_dir()


def load_pack(slug: str) -> GenrePack:
    """Load a genre pack by slug from the sidequest-content tree."""
    return load_genre_pack(CONTENT_ROOT / slug)


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_caverns_and_claudes_pack_loads_with_dual_dial_schema():
    pack = load_pack("caverns_and_claudes")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_heavy_metal_pack_loads_with_dual_dial_schema():
    pack = load_pack("heavy_metal")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_space_opera_pack_loads_with_dual_dial_schema():
    pack = load_pack("space_opera")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_spaghetti_western_pack_loads_with_dual_dial_schema():
    pack = load_pack("spaghetti_western")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_mutant_wasteland_pack_loads_with_dual_dial_schema():
    pack = load_pack("mutant_wasteland")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}


@pytest.mark.skipif(not _has_real_content(), reason="sidequest-content not on disk")
def test_elemental_harmony_pack_loads_with_dual_dial_schema():
    pack = load_pack("elemental_harmony")
    assert pack.rules is not None
    for cdef in pack.rules.confrontations:
        assert cdef.player_metric.threshold > 0
        assert cdef.opponent_metric.threshold > 0
        for beat in cdef.beats:
            kind = beat.kind.value if hasattr(beat.kind, "value") else beat.kind
            assert kind in {"strike", "brace", "push", "angle"}
