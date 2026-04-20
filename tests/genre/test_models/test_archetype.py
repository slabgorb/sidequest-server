"""Tests for ArchetypeResolved — the only production LayeredMerge type in Phase 1.

Includes the mandatory wiring test per CLAUDE.md.
"""

from __future__ import annotations

from sidequest.genre.models.archetype import ArchetypeResolved
from sidequest.genre.resolver import LayeredMerge


# ---------------------------------------------------------------------------
# Wiring test — CLAUDE.md requirement: every Layered type needs a wiring test
# ---------------------------------------------------------------------------


def test_archetype_resolved_layered_merge_fields_declared() -> None:
    """Every field on ArchetypeResolved has a declared merge strategy (no silent defaults).

    Mandated by CLAUDE.md: "Every Test Suite Needs a Wiring Test."
    If a field is added without json_schema_extra={"merge": ...}, this test catches it.
    """
    for field_name, field_info in ArchetypeResolved.model_fields.items():
        extra = field_info.json_schema_extra or {}
        assert isinstance(extra, dict) and "merge" in extra, (
            f"ArchetypeResolved.{field_name} has no merge strategy declared. "
            f"Add Field(json_schema_extra={{'merge': 'replace'}})."
        )


def test_archetype_resolved_is_layered_merge_subclass() -> None:
    """ArchetypeResolved participates in the LayeredMerge protocol."""
    assert issubclass(ArchetypeResolved, LayeredMerge)


# ---------------------------------------------------------------------------
# Field defaults
# ---------------------------------------------------------------------------


def test_archetype_resolved_default_construction() -> None:
    """ArchetypeResolved constructs with all-default values."""
    ar = ArchetypeResolved()
    assert ar.name == ""
    assert ar.jungian == ""
    assert ar.rpg_role == ""
    assert ar.npc_role is None
    assert ar.speech_pattern == ""
    assert ar.lore == ""
    assert ar.faction is None
    assert ar.cultural_status is None


# ---------------------------------------------------------------------------
# Merge semantics — all fields use replace
# ---------------------------------------------------------------------------


def test_archetype_resolved_merge_replace_all_fields() -> None:
    """Deeper tier replaces all string fields."""
    base = ArchetypeResolved(
        name="Base Sage",
        jungian="sage",
        rpg_role="support",
        speech_pattern="formal",
        lore="In the old days...",
    )
    deeper = ArchetypeResolved(
        name="World Sage",
        jungian="sage",
        rpg_role="healer",
        speech_pattern="archaic",
        lore="Per the Thornwall annals...",
    )
    merged = base.merge(deeper)
    assert merged.name == "World Sage"
    assert merged.rpg_role == "healer"
    assert merged.speech_pattern == "archaic"
    assert merged.lore == "Per the Thornwall annals..."


def test_archetype_resolved_merge_optional_fields() -> None:
    """Optional fields (npc_role, faction, cultural_status) merge as replace."""
    base = ArchetypeResolved(npc_role="mentor", faction="Thornwall", cultural_status="elder")
    deeper = ArchetypeResolved(npc_role="antagonist", faction=None, cultural_status="outcast")
    merged = base.merge(deeper)
    assert merged.npc_role == "antagonist"
    assert merged.faction is None  # deeper wins even with None (Phase D limitation)
    assert merged.cultural_status == "outcast"


def test_archetype_resolved_merge_returns_same_type() -> None:
    """merge() returns an ArchetypeResolved, not a base LayeredMerge."""
    base = ArchetypeResolved(name="A")
    deeper = ArchetypeResolved(name="B")
    merged = base.merge(deeper)
    assert type(merged) is ArchetypeResolved


# ---------------------------------------------------------------------------
# Pydantic model identity
# ---------------------------------------------------------------------------


def test_archetype_resolved_field_count() -> None:
    """ArchetypeResolved has exactly 8 fields, matching the Rust source."""
    assert len(ArchetypeResolved.model_fields) == 8


def test_archetype_resolved_field_names() -> None:
    """All 8 field names match the Rust struct."""
    expected = {
        "name",
        "jungian",
        "rpg_role",
        "npc_role",
        "speech_pattern",
        "lore",
        "faction",
        "cultural_status",
    }
    assert set(ArchetypeResolved.model_fields.keys()) == expected
