"""Integration test: caverns_and_claudes pack loads under dual-dial momentum schema.

Verifies that the migrated rules.yaml round-trips through GenrePack validation with
the dual-dial ConfrontationDef schema (player_metric + opponent_metric, BeatDef.kind).

See Task 27 — canary migration for dual-track momentum Phase 3.
"""

from __future__ import annotations

import pytest

from sidequest.genre.error import PackError
from sidequest.genre.loader import load_genre_pack
from sidequest.genre.models.pack import GenrePack
from tests._helpers.genre_paths import GENRE_PACKS_DIR, find_pack_path

CONTENT_ROOT = GENRE_PACKS_DIR
CC_PACK_DIR = CONTENT_ROOT / "caverns_and_claudes"


def _has_real_content() -> bool:
    return CC_PACK_DIR.is_dir()


def load_pack(slug: str) -> GenrePack:
    """Load a genre pack by slug from the sidequest-content tree."""
    return load_genre_pack(find_pack_path(slug))


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


# ---------------------------------------------------------------------------
# Task 5: class_filter / encounter_beat_choices cross-reference validation
# ---------------------------------------------------------------------------


def test_pack_load_rejects_dangling_class_filter(tmp_path, minimal_pack_factory):
    """class_filter must reference a class declared in classes.yaml."""
    pack = minimal_pack_factory(tmp_path)
    pack.set_rules_yaml(
        confrontations=[
            {
                "type": "combat",
                "label": "C",
                "category": "combat",
                "player_metric": {"name": "m", "starting": 0, "threshold": 7},
                "opponent_metric": {"name": "m", "starting": 0, "threshold": 7},
                "beats": [
                    {
                        "id": "ghost_strike",
                        "label": "X",
                        "kind": "strike",
                        "stat_check": "STR",
                        "class_filter": ["Necromancer"],  # not in classes.yaml
                    }
                ],
            }
        ],
        allowed_classes=["Fighter"],
    )
    pack.set_classes_yaml(
        [
            {
                "id": "fighter",
                "display_name": "Fighter",
                "rpg_role": "tank",
                "jungian_default": "hero",
                "prime_requisite": "STR",
                "minimum_score": 9,
                "kit_table": "fighter_kit",
                "flavor": "—",
                "encounter_beat_choices": ["ghost_strike"],
            }
        ]
    )
    with pytest.raises(PackError, match="class_filter.*Necromancer.*not declared"):
        load_genre_pack(pack.path)


def test_pack_load_rejects_dangling_encounter_beat_choice(tmp_path, minimal_pack_factory):
    """encounter_beat_choices must reference a beat that exists in some pool."""
    pack = minimal_pack_factory(tmp_path)
    pack.set_rules_yaml(
        confrontations=[
            {
                "type": "combat",
                "label": "C",
                "category": "combat",
                "player_metric": {"name": "m", "starting": 0, "threshold": 7},
                "opponent_metric": {"name": "m", "starting": 0, "threshold": 7},
                "beats": [{"id": "attack", "label": "A", "kind": "strike", "stat_check": "STR"}],
            }
        ],
        allowed_classes=["Fighter"],
    )
    pack.set_classes_yaml(
        [
            {
                "id": "fighter",
                "display_name": "Fighter",
                "rpg_role": "tank",
                "jungian_default": "hero",
                "prime_requisite": "STR",
                "minimum_score": 9,
                "kit_table": "fighter_kit",
                "flavor": "—",
                "encounter_beat_choices": ["attack", "smite"],  # smite missing
            }
        ]
    )
    with pytest.raises(PackError, match="encounter_beat_choices.*smite.*not in pool"):
        load_genre_pack(pack.path)


def test_pack_load_rejects_empty_encounter_beat_choices_for_allowed_class(
    tmp_path, minimal_pack_factory
):
    """A class in allowed_classes must have a non-empty encounter_beat_choices.

    TODO(task-14): once Task 14 lands content with non-empty per-class beat lists,
    this validator and the C&C pack will both go green together.
    """
    pack = minimal_pack_factory(tmp_path)
    pack.set_rules_yaml(
        confrontations=[
            {
                "type": "combat",
                "label": "C",
                "category": "combat",
                "player_metric": {"name": "m", "starting": 0, "threshold": 7},
                "opponent_metric": {"name": "m", "starting": 0, "threshold": 7},
                "beats": [{"id": "attack", "label": "A", "kind": "strike", "stat_check": "STR"}],
            }
        ],
        allowed_classes=["Fighter"],
    )
    pack.set_classes_yaml(
        [
            {
                "id": "fighter",
                "display_name": "Fighter",
                "rpg_role": "tank",
                "jungian_default": "hero",
                "prime_requisite": "STR",
                "minimum_score": 9,
                "kit_table": "fighter_kit",
                "flavor": "—",
                "encounter_beat_choices": [],
            }
        ]
    )
    with pytest.raises(PackError, match="encounter_beat_choices.*empty"):
        load_genre_pack(pack.path)
