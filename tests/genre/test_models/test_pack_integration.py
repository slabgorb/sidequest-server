"""Integration tests: full Caverns & Claudes pack deserialization.

This is the most critical test in the suite. It verifies that every model
can accept real production data from the actual genre pack YAML files.

If this test passes, the models layer is correct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from sidequest.genre.models import (
    ArchetypeConstraints,
    ArchetypeFunnels,
    AudioConfig,
    AxesConfig,
    BackstoryTables,
    BeatVocabulary,
    CartographyConfig,
    CharCreationScene,
    Culture,
    DramaThresholds,
    GenrePack,
    GenreTheme,
    InventoryConfig,
    Legend,
    Lore,
    NpcArchetype,
    PackMeta,
    PowerTier,
    ProgressionConfig,
    Prompts,
    RulesConfig,
    TropeDefinition,
    VisualStyle,
    WorldConfig,
    WorldLore,
)

CONTENT_ROOT = Path(__file__).resolve().parents[4] / "sidequest-content" / "genre_packs"
CC = CONTENT_ROOT / "caverns_and_claudes"
# caverns_sunden — the leaf-shaped world that replaces caverns_three_sins
# after the 2026-05-06 hub-world revert. Carries cartography, openings,
# archetypes, archetype_funnels, pacing, lore, legends, history,
# factions, hamlet, audio, visual_style, portrait_manifest at world root.
SUNDEN = CC / "worlds" / "caverns_sunden"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# Individual file deserialization tests
# ─────────────────────────────────────────────────────────────────────────────


def test_pack_meta_deserializes() -> None:
    meta = PackMeta.model_validate(_load(CC / "pack.yaml"))
    assert meta.name == "Caverns & Claudes"
    assert meta.version  # semver string; content release tags bump this
    assert meta.recommended_players is not None
    assert meta.recommended_players.min == 2
    assert meta.recommended_players.max == 6


def test_lore_deserializes() -> None:
    lore = Lore.model_validate(_load(CC / "lore.yaml"))
    assert lore.world_name == "Caverns & Claudes"
    assert len(lore.history) > 0
    assert len(lore.cosmology) > 0


def test_rules_deserializes() -> None:
    rules = RulesConfig.model_validate(_load(CC / "rules.yaml"))
    assert rules.stat_generation == "roll_3d6_strict"
    assert "STR" in rules.ability_score_names
    assert len(rules.confrontations) == 3
    # Verify confrontation structure
    combat = next(c for c in rules.confrontations if c.confrontation_type == "combat")
    assert combat.category == "combat"
    assert len(combat.beats) >= 4


def test_axes_deserializes() -> None:
    axes = AxesConfig.model_validate(_load(CC / "axes.yaml"))
    assert len(axes.definitions) == 3
    assert len(axes.presets) == 3
    comedy = next(d for d in axes.definitions if d.id == "comedy")
    assert comedy.default == 0.3


def test_theme_deserializes() -> None:
    theme = GenreTheme.model_validate(_load(CC / "theme.yaml"))
    assert theme.primary == "#D4A843"
    assert theme.dinkus.enabled is True
    assert theme.session_opener.enabled is True


def test_progression_deserializes() -> None:
    prog = ProgressionConfig.model_validate(_load(CC / "progression.yaml"))
    assert len(prog.affinities) == 5
    delver = next(a for a in prog.affinities if a.name == "Delver")
    assert delver.unlocks is not None
    assert delver.unlocks.tier_1 is not None
    assert len(delver.unlocks.tier_1.abilities) == 2


def test_audio_deserializes() -> None:
    audio = AudioConfig.model_validate(_load(CC / "audio.yaml"))
    assert "exploration" in audio.mood_tracks
    assert len(audio.mood_tracks["exploration"]) > 0
    assert audio.mixer.music_volume == pytest.approx(0.3)
    assert len(audio.themes) > 0


def test_prompts_deserializes() -> None:
    prompts = Prompts.model_validate(_load(CC / "prompts.yaml"))
    assert len(prompts.narrator) > 100
    assert len(prompts.combat) > 10
    assert len(prompts.npc) > 10


def test_tropes_deserializes() -> None:
    tropes_data = _load(CC / "tropes.yaml")
    assert isinstance(tropes_data, list)
    tropes = [TropeDefinition.model_validate(t) for t in tropes_data]
    # 4 original genre tropes + 4 Plan 7 §14.A genre-level set-piece tropes
    # referenced by the Plan-4 themes' set-pieces.
    assert len(tropes) == 8
    assert {
        "the_thing_that_followed_you_down",
        "the_keeper_notices_the_disturbance",
        "priest_demands_a_sacrifice",
        "the_resource_clock_you_can_see",
    } <= {t.id for t in tropes}
    keeper_stirs = next(t for t in tropes if t.id == "the_keeper_stirs")
    assert keeper_stirs.passive_progression is not None
    assert keeper_stirs.passive_progression.rate_per_turn == pytest.approx(0.02)


def test_visual_style_deserializes() -> None:
    vs = VisualStyle.model_validate(_load(CC / "visual_style.yaml"))
    assert len(vs.positive_suffix) > 0


def test_archetypes_deserializes() -> None:
    arc_data = _load(CC / "archetypes.yaml")
    assert isinstance(arc_data, list)
    archetypes = [NpcArchetype.model_validate(a) for a in arc_data]
    assert len(archetypes) == 11
    # C&C archetypes have extra genre-specific fields — verify they load without error


def test_char_creation_deserializes() -> None:
    data = _load(CC / "char_creation.yaml")
    assert isinstance(data, list)
    scenes = [CharCreationScene.model_validate(s) for s in data]
    # 6 scenes (visible-dice era): the_roll, the_arrangement,
    # the_calling, the_story, the_kit, the_mouth.
    assert len(scenes) == 6


def test_cultures_deserializes() -> None:
    data = _load(CC / "cultures.yaml")
    assert isinstance(data, list)
    cultures = [Culture.model_validate(c) for c in data]
    assert len(cultures) == 3


def test_inventory_deserializes() -> None:
    inv = InventoryConfig.model_validate(_load(CC / "inventory.yaml"))
    # 30 items: 23 base + 7 class-kit items (sword_long, staff_wood,
    # hammer_war, spellbook, component_pouch, holy_symbol, lockpicks)
    # added for the classic-class chargen system.
    assert len(inv.item_catalog) == 30
    assert inv.currency is not None


def test_backstory_tables_deserializes() -> None:
    bst = BackstoryTables.model_validate(_load(CC / "backstory_tables.yaml"))
    assert "{trade}" in bst.template
    assert "trade" in bst.tables
    assert len(bst.tables["trade"]) > 0


def test_beat_vocabulary_deserializes() -> None:
    bv = BeatVocabulary.model_validate(_load(CC / "beat_vocabulary.yaml"))
    assert len(bv.obstacles) == 17
    con_obstacles = [o for o in bv.obstacles if o.stat_check == "CON"]
    assert len(con_obstacles) >= 5, (
        "caverns_and_claudes should have ≥5 CON obstacles (story 39-9 rebalance)"
    )


def test_power_tiers_deserializes() -> None:
    pt_data = _load(CC / "power_tiers.yaml")
    assert isinstance(pt_data, dict)
    power_tiers = {}
    for class_name, tiers in pt_data.items():
        power_tiers[class_name] = [PowerTier.model_validate(t) for t in tiers]
    assert "Delver" in power_tiers
    assert len(power_tiers["Delver"]) == 3


def test_archetype_constraints_deserializes() -> None:
    ac = ArchetypeConstraints.model_validate(_load(CC / "archetype_constraints.yaml"))
    assert len(ac.valid_pairings.common) > 0


# ─────────────────────────────────────────────────────────────────────────────
# World-level YAML tests (caverns_sunden)
# ─────────────────────────────────────────────────────────────────────────────


def test_sunden_world_config_deserializes() -> None:
    wc = WorldConfig.model_validate(_load(SUNDEN / "world.yaml"))
    assert wc.name == "Hamlet of Sünden"
    assert wc.cover_poi == "sunden_square"


def test_sunden_world_lore_deserializes() -> None:
    wl = WorldLore.model_validate(_load(SUNDEN / "lore.yaml"))
    assert wl is not None


def test_sunden_legends_deserializes() -> None:
    data = _load(SUNDEN / "legends.yaml")
    assert isinstance(data, list)
    legs = [Legend.model_validate(leg) for leg in data]
    assert len(legs) >= 1


def test_sunden_cartography_deserializes() -> None:
    cart = CartographyConfig.model_validate(_load(SUNDEN / "cartography.yaml"))
    assert len(cart.regions) >= 4
    assert "sunden_hamlet" in cart.regions


def test_sunden_archetype_funnels_deserializes() -> None:
    funnels = ArchetypeFunnels.model_validate(_load(SUNDEN / "archetype_funnels.yaml"))
    assert len(funnels.funnels) >= 1


def test_sunden_archetypes_deserializes() -> None:
    data = _load(SUNDEN / "archetypes.yaml")
    arcs = [NpcArchetype.model_validate(a) for a in data]
    assert len(arcs) >= 1


def test_sunden_openings_deserializes() -> None:
    raw = _load(SUNDEN / "openings.yaml")
    assert isinstance(raw, dict)
    assert "openings" in raw
    assert isinstance(raw["openings"], list)
    assert len(raw["openings"]) >= 1


def test_sunden_pacing_deserializes() -> None:
    raw = _load(SUNDEN / "pacing.yaml")
    drama_raw = raw.get("drama_thresholds", raw)  # type: ignore[union-attr]
    thresholds = DramaThresholds.model_validate(drama_raw)
    assert thresholds.render_threshold > 0


# ─────────────────────────────────────────────────────────────────────────────
# GenrePack aggregate test (manually assembled — loader is Story 41-3)
# ─────────────────────────────────────────────────────────────────────────────


def test_genre_pack_assembles_from_caverns_and_claudes() -> None:
    """Verify GenrePack can be assembled from real Caverns & Claudes data.

    The loader (Story 41-3) will handle this automatically. Here we test that
    all fields accept real data by constructing GenrePack directly.
    """
    meta = PackMeta.model_validate(_load(CC / "pack.yaml"))
    rules = RulesConfig.model_validate(_load(CC / "rules.yaml"))
    lore = Lore.model_validate(_load(CC / "lore.yaml"))
    theme = GenreTheme.model_validate(_load(CC / "theme.yaml"))
    visual_style = VisualStyle.model_validate(_load(CC / "visual_style.yaml"))
    progression = ProgressionConfig.model_validate(_load(CC / "progression.yaml"))
    axes = AxesConfig.model_validate(_load(CC / "axes.yaml"))
    audio = AudioConfig.model_validate(_load(CC / "audio.yaml"))
    prompts = Prompts.model_validate(_load(CC / "prompts.yaml"))

    archetypes = [NpcArchetype.model_validate(a) for a in _load(CC / "archetypes.yaml")]
    cultures = [Culture.model_validate(c) for c in _load(CC / "cultures.yaml")]
    char_creation = [CharCreationScene.model_validate(s) for s in _load(CC / "char_creation.yaml")]
    tropes = [TropeDefinition.model_validate(t) for t in _load(CC / "tropes.yaml")]

    beat_vocabulary = BeatVocabulary.model_validate(_load(CC / "beat_vocabulary.yaml"))
    inventory = InventoryConfig.model_validate(_load(CC / "inventory.yaml"))
    backstory_tables = BackstoryTables.model_validate(_load(CC / "backstory_tables.yaml"))
    archetype_constraints = ArchetypeConstraints.model_validate(
        _load(CC / "archetype_constraints.yaml")
    )

    pt_data = _load(CC / "power_tiers.yaml")
    power_tiers = {k: [PowerTier.model_validate(t) for t in v] for k, v in pt_data.items()}  # type: ignore[union-attr]

    pack = GenrePack(
        meta=meta,
        rules=rules,
        lore=lore,
        theme=theme,
        archetypes=archetypes,
        char_creation=char_creation,
        visual_style=visual_style,
        progression=progression,
        axes=axes,
        audio=audio,
        cultures=cultures,
        prompts=prompts,
        tropes=tropes,
        beat_vocabulary=beat_vocabulary,
        inventory=inventory,
        backstory_tables=backstory_tables,
        archetype_constraints=archetype_constraints,
        power_tiers=power_tiers,
    )

    assert pack.name == "Caverns & Claudes"
    assert len(pack.archetypes) == 11
    assert len(pack.tropes) == 4
    assert len(pack.cultures) == 3
    assert pack.beat_vocabulary is not None
    assert len(pack.beat_vocabulary.obstacles) == 17
    assert pack.inventory is not None
    assert pack.archetype_constraints is not None


def test_all_worlds_load() -> None:
    """Verify every caverns_and_claudes world deserializes — world.yaml
    and cartography.yaml at the world level. (The hub-world / per-dungeon
    cartography variant was reverted 2026-05-06; all C&C worlds are leaf
    worlds now.)
    """
    worlds_dir = CC / "worlds"
    world_names = [d.name for d in worlds_dir.iterdir() if d.is_dir()]
    assert len(world_names) >= 1
    for world_name in world_names:
        wdir = worlds_dir / world_name
        wc = WorldConfig.model_validate(_load(wdir / "world.yaml"))
        assert len(wc.name) > 0
        cart = CartographyConfig.model_validate(_load(wdir / "cartography.yaml"))
        assert cart is not None
