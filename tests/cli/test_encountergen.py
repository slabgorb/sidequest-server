"""Tests for ``sidequest.cli.encountergen``.

Covers the schema-flatten for the post-fold ``regions.{region}.creatures``
shape, creature → EnemyBlock translation, tier-mapped abilities/weaknesses,
power-tier lookup, JSON output serialization, and end-to-end CLI invocation
against the real ``caverns_and_claudes / caverns_sunden`` bestiary.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sidequest.cli.encountergen.encountergen import (
    _collect_creatures_from_yaml,
    _enemy_block_to_dict,
    build_visual_prompt,
    creature_to_enemy_block,
    find_power_tier,
    generate_abilities,
    generate_weaknesses,
    main,
    tier_to_level_range,
)
from sidequest.genre.models.character import NpcArchetype
from sidequest.genre.models.narrative import PowerTier

CONTENT_ROOT = Path(__file__).resolve().parents[2].parent / "sidequest-content" / "genre_packs"


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (1, (1, 3)),
        (2, (4, 6)),
        (3, (7, 9)),
        (4, (10, 10)),
        (99, (1, 3)),  # default fallback
    ],
)
def test_tier_to_level_range(tier: int, expected: tuple[int, int]) -> None:
    assert tier_to_level_range(tier) == expected


def test_find_power_tier_matches_level_range() -> None:
    tiers = [
        PowerTier(level_range=[1, 3], label="tier-1", player="p1", npc="n1"),
        PowerTier(level_range=[4, 6], label="tier-2", player="p2", npc="n2"),
    ]
    power_tiers = {"Scavenger": tiers}
    assert find_power_tier(power_tiers, "Scavenger", 2).label == "tier-1"  # type: ignore[union-attr]
    assert find_power_tier(power_tiers, "Scavenger", 5).label == "tier-2"  # type: ignore[union-attr]
    assert find_power_tier(power_tiers, "Scavenger", 99) is None
    assert find_power_tier(power_tiers, "Unknown", 1) is None


# ---------------------------------------------------------------------------
# Schema flatten — _collect_creatures_from_yaml
# ---------------------------------------------------------------------------


def test_collect_creatures_legacy_top_level_schema(tmp_path: Path) -> None:
    """Pre-fold schema with top-level ``creatures:`` still works."""
    yaml_path = tmp_path / "creatures.yaml"
    yaml_path.write_text(
        "creatures:\n  - id: a\n    name: A\n  - id: b\n    name: B\n",
        encoding="utf-8",
    )
    out = _collect_creatures_from_yaml(yaml_path)
    assert [c["id"] for c in out] == ["a", "b"]


def test_collect_creatures_nested_regions_schema(tmp_path: Path) -> None:
    """Post-fold (2026-05-10) schema ``regions.{region}.creatures`` flattens across regions."""
    yaml_path = tmp_path / "creatures.yaml"
    yaml_path.write_text(
        "regions:\n"
        "  alpha:\n"
        "    name: Alpha\n"
        "    creatures:\n"
        "      - id: a1\n"
        "        name: A1\n"
        "      - id: a2\n"
        "        name: A2\n"
        "  beta:\n"
        "    name: Beta\n"
        "    creatures:\n"
        "      - id: b1\n"
        "        name: B1\n",
        encoding="utf-8",
    )
    out = _collect_creatures_from_yaml(yaml_path)
    ids = {c["id"] for c in out}
    assert ids == {"a1", "a2", "b1"}


def test_collect_creatures_missing_file_returns_empty(tmp_path: Path) -> None:
    out = _collect_creatures_from_yaml(tmp_path / "absent.yaml")
    assert out == []


def test_collect_creatures_malformed_yaml_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    yaml_path = tmp_path / "creatures.yaml"
    yaml_path.write_text("regions: [unterminated", encoding="utf-8")
    out = _collect_creatures_from_yaml(yaml_path)
    assert out == []
    assert "failed to parse" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# creature_to_enemy_block
# ---------------------------------------------------------------------------


def test_creature_to_enemy_block_translates_fields() -> None:
    rng = random.Random(0)
    creature = {
        "id": "chalk_moth",
        "name": "Chalk Moth",
        "threat_level": 1,
        "hp": 1,
        "ac": 10,
        "damage": "0 (color drain)",
        "morale": "cowardly",
        "abilities": [
            {"name": "Color Feed", "description": "Drains pigment from organic surfaces."},
            {"name": "Shimmer Cloud", "description": "Disorienting in groups."},
        ],
        "tags": ["level_1", "swarm"],
        "loot": [{"description": "Wing dust"}],
        "description": "Finger-length moths that bleach color from cloth.",
    }
    block = creature_to_enemy_block(creature, rng)

    assert block.name == "Chalk Moth"
    assert block.class_ == "creature"
    assert block.level == 1
    assert block.tier_label == "tier-1"
    assert block.race == "level_1"  # first tag
    assert block.hp == 1
    assert block.role == "0 (color drain), morale: cowardly"
    assert block.weaknesses == ["AC 10"]
    assert block.disposition == -20
    assert block.inventory == ["Wing dust"]
    assert any(a.startswith("Color Feed —") for a in block.abilities)
    assert block.ocean_summary == "feral and aggressive"
    assert block.visual_prompt.startswith("Finger-length moths")
    assert len(block.visual_prompt) <= 200


def test_creature_to_enemy_block_defaults_when_fields_missing() -> None:
    rng = random.Random(0)
    block = creature_to_enemy_block({"name": "Nameless"}, rng)
    assert block.name == "Nameless"
    assert block.level == 1  # threat_level default
    assert block.hp == 4  # hp default
    assert block.weaknesses == ["AC 10"]  # ac default
    assert block.role == ""  # damage + morale both empty
    assert block.race == "beast"  # no tags
    assert block.abilities == []
    assert block.inventory == []


def test_creature_to_enemy_block_ability_description_truncated() -> None:
    rng = random.Random(0)
    long_desc = "x" * 200
    block = creature_to_enemy_block(
        {"name": "X", "abilities": [{"name": "Long", "description": long_desc}]},
        rng,
    )
    # Format is "<name> — <truncated to 80 chars>"
    assert block.abilities[0].startswith("Long — ")
    assert len(block.abilities[0]) <= len("Long — ") + 80


# ---------------------------------------------------------------------------
# generate_abilities + generate_weaknesses
# ---------------------------------------------------------------------------


def _archetype(name: str = "Test", typical_classes: list[str] | None = None) -> NpcArchetype:
    return NpcArchetype(
        name=name,
        description="d",
        typical_classes=typical_classes or [],
    )


def test_generate_abilities_tier_indexes_into_class_pool() -> None:
    rng = random.Random(42)
    abilities = generate_abilities("scavenger", tier=2, archetype=_archetype(), rng=rng)
    # Tier 2 draws from tier-1 and tier-2 scavenger pools (Rust shape).
    expected_pool = {
        "Scrap Throw",
        "Quick Loot",
        "Improvised Trap",
        "Ambush",
        "Jury-Rig Weapon",
        "Escape Artist",
    }
    assert all(a in expected_pool for a in abilities)


def test_generate_abilities_unknown_class_uses_generic_pool() -> None:
    rng = random.Random(1)
    abilities = generate_abilities("nonexistent_class", tier=1, archetype=_archetype(), rng=rng)
    generic_t1 = {"Strike", "Defend", "Retreat"}
    assert any(a in generic_t1 for a in abilities)


def test_generate_abilities_appends_archetype_instinct_on_class_match() -> None:
    rng = random.Random(1)
    archetype = _archetype("Wasteland Trader", typical_classes=["Scavenger"])
    abilities = generate_abilities("Scavenger", tier=1, archetype=archetype, rng=rng)
    assert "Wasteland Trader's Instinct" in abilities


def test_generate_abilities_no_instinct_when_class_mismatch() -> None:
    rng = random.Random(1)
    archetype = _archetype("Wasteland Trader", typical_classes=["Synth"])
    abilities = generate_abilities("Scavenger", tier=1, archetype=archetype, rng=rng)
    assert all(not a.endswith("'s Instinct") for a in abilities)


def test_generate_weaknesses_class_known() -> None:
    weaknesses = generate_weaknesses("synth", "synthetic", random.Random(0))
    assert any("EMP" in w for w in weaknesses)


def test_generate_weaknesses_class_unknown_defaults() -> None:
    rng = random.Random(0)
    # Force rng.randrange(2) → 1 so race weakness skipped (random.Random(0) gives 1 first)
    weaknesses = generate_weaknesses("unknown_class", "human", rng)
    assert weaknesses[0] == "no special resistances"


# ---------------------------------------------------------------------------
# _enemy_block_to_dict
# ---------------------------------------------------------------------------


def test_enemy_block_to_dict_renames_class_field() -> None:
    rng = random.Random(0)
    block = creature_to_enemy_block({"name": "X", "tags": ["tag1"]}, rng)
    data = _enemy_block_to_dict(block)
    assert "class" in data
    assert "class_" not in data
    assert data["class"] == "creature"


# ---------------------------------------------------------------------------
# build_visual_prompt
# ---------------------------------------------------------------------------


def test_build_visual_prompt_uses_npc_description_when_available() -> None:
    pack = _minimal_pack_for_visual_prompt(positive_suffix="cinematic suffix")
    archetype = _archetype("Trader", typical_classes=["Scavenger"])
    prompt = build_visual_prompt(pack, "Scavenger", level=1, archetype=archetype, context=None)
    assert "wasteland trader figure" in prompt
    assert prompt.endswith("cinematic suffix")


def test_build_visual_prompt_threads_context() -> None:
    pack = _minimal_pack_for_visual_prompt()
    archetype = _archetype("Trader", typical_classes=["Scavenger"])
    prompt = build_visual_prompt(
        pack, "Scavenger", level=1, archetype=archetype, context="guarding a bridge"
    )
    assert "guarding a bridge" in prompt


def _minimal_pack_for_visual_prompt(positive_suffix: str = "suffix") -> Any:
    """Build a minimal GenrePack-like stub with the fields build_visual_prompt reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        power_tiers={
            "Scavenger": [
                PowerTier(
                    level_range=[1, 3],
                    label="tier-1",
                    player="player desc",
                    npc="wasteland trader figure",
                )
            ]
        },
        visual_style=SimpleNamespace(positive_suffix=positive_suffix),
    )


# ---------------------------------------------------------------------------
# End-to-end CLI against real C&C Sünden bestiary
# ---------------------------------------------------------------------------


def _real_content_available() -> bool:
    return (
        CONTENT_ROOT / "caverns_and_claudes" / "worlds" / "caverns_sunden" / "creatures.yaml"
    ).exists()


@pytest.mark.skipif(not _real_content_available(), reason="sidequest-content not checked out")
def test_e2e_caverns_sunden_tier_one_returns_only_tier_one_creatures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke test: --world caverns_sunden --tier 1 returns only threat_level=1 creatures."""
    rc = main(
        [
            "--genre-packs-path",
            str(CONTENT_ROOT),
            "--genre",
            "caverns_and_claudes",
            "--world",
            "caverns_sunden",
            "--tier",
            "1",
            "--count",
            "3",
        ]
    )
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["enemies"]) == 3
    for enemy in output["enemies"]:
        assert enemy["tier_label"] == "tier-1"
        assert enemy["class"] == "creature"
        assert enemy["disposition"] == -20


@pytest.mark.skipif(not _real_content_available(), reason="sidequest-content not checked out")
def test_e2e_caverns_sunden_pulls_from_multiple_regions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bulk run should sample creatures from multiple regions of the folded bestiary."""
    rc = main(
        [
            "--genre-packs-path",
            str(CONTENT_ROOT),
            "--genre",
            "caverns_and_claudes",
            "--world",
            "caverns_sunden",
            "--count",
            "20",
        ]
    )
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    # Sünden has chalk_moth (grimvault), ledger_worm (horden), and others across regions.
    # 20 samples should hit at least two distinct names; the flatten is broken if not.
    names = {e["name"] for e in output["enemies"]}
    assert len(names) >= 2


# ---------------------------------------------------------------------------
# python -m sidequest.cli.encountergen as a subprocess
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _real_content_available(), reason="sidequest-content not checked out")
def test_module_runnable_via_dash_m() -> None:
    """The package is callable with ``python -m sidequest.cli.encountergen``."""
    result = subprocess.run(  # noqa: S603 — controlled args
        [
            sys.executable,
            "-m",
            "sidequest.cli.encountergen",
            "--genre-packs-path",
            str(CONTENT_ROOT),
            "--genre",
            "caverns_and_claudes",
            "--world",
            "caverns_sunden",
            "--tier",
            "1",
            "--count",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "enemies" in payload
    assert len(payload["enemies"]) == 1
