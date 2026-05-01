"""Unified genre pack loader.

Port of sidequest-genre/src/loader.rs (638 LOC).

A single function loads an entire genre pack from a directory, reading all
YAML files and assembling them into a typed GenrePack. A GenreLoader class
supports multi-path search (local → home → install).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sidequest.genre.cache import GenreCache
from sidequest.genre.error import GenreLoadError, GenreNotFoundError
from sidequest.genre.genre_code import GenreCode
from sidequest.genre.models.archetype_axes import BaseArchetypes
from sidequest.genre.models.archetype_constraints import ArchetypeConstraints
from sidequest.genre.models.archetype_funnels import ArchetypeFunnels
from sidequest.genre.models.audio import AudioConfig, VoicePresets
from sidequest.genre.models.axes import AxesConfig
from sidequest.genre.models.character import (
    BackstoryTables,
    CharCreationScene,
    EquipmentTables,
    NpcArchetype,
    VisualStyle,
)
from sidequest.genre.models.chassis import ChassisClassesConfig
from sidequest.genre.models.culture import Culture
from sidequest.genre.models.inventory import InventoryConfig
from sidequest.genre.models.legends import Legend
from sidequest.genre.models.lore import Lore, WorldLore
from sidequest.genre.models.narrative import (
    Achievement,
    BeatVocabulary,
    Opening,
    PowerTier,
    Prompts,
)
from sidequest.genre.models.npc_traits import NpcTraitsDatabase
from sidequest.genre.models.ocean import DramaThresholds
from sidequest.genre.models.pack import GenrePack, PackMeta, PortraitManifestEntry, World
from sidequest.genre.models.progression import ProgressionConfig
from sidequest.genre.models.rules import RulesConfig
from sidequest.genre.models.scenario import ScenarioNpc, ScenarioPack
from sidequest.genre.models.theme import GenreTheme
from sidequest.genre.models.tropes import TropeDefinition
from sidequest.genre.models.world import CartographyConfig, NavigationMode, WorldConfig
from sidequest.genre.resolve import resolve_trope_inheritance

# ---------------------------------------------------------------------------
# Default search paths (mirrors Rust loader convention)
# ---------------------------------------------------------------------------

DEFAULT_GENRE_PACK_SEARCH_PATHS: list[Path] = [
    # Orchestrator root / sidequest-content — the canonical dev layout.
    # __file__ = sidequest-server/sidequest/genre/loader.py; parents[3] = orchestrator root.
    Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs",
    # CWD fallbacks for when the server is run from elsewhere.
    Path.cwd() / "sidequest-content" / "genre_packs",
    Path.cwd().parent / "sidequest-content" / "genre_packs",
    Path.home() / ".sidequest" / "genre_packs",
]


# ---------------------------------------------------------------------------
# Low-level YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml[T](path: Path, type_: type[T]) -> T:
    """Load and parse a required YAML file.

    Port of Rust load_yaml<T>(path). Required; raises GenreLoadError on any
    failure (file missing, unreadable, or schema mismatch). No silent fallbacks.

    Raises:
        GenreLoadError: If the file cannot be read or parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise GenreLoadError(path=path, detail=str(e)) from e

    try:
        raw = yaml.safe_load(text)
        return type_.model_validate(raw)  # type: ignore[attr-defined]
    except Exception as e:
        raise GenreLoadError(path=path, detail=str(e)) from e


def _load_yaml_optional[T](path: Path, type_: type[T]) -> T | None:
    """Load and parse an optional YAML file. Returns None if file doesn't exist.

    Port of Rust load_yaml_optional<T>(path). If present, failure is still loud.

    Raises:
        GenreLoadError: If the file exists but cannot be read or parsed.
    """
    if not path.exists():
        return None
    return _load_yaml(path, type_)


def _load_yaml_raw(path: Path) -> Any:
    """Load raw YAML as a Python object (no model validation).

    Used for flexible-schema files (legends, visual_style, history).

    Raises:
        GenreLoadError: If the file cannot be read or parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise GenreLoadError(path=path, detail=str(e)) from e

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise GenreLoadError(path=path, detail=str(e)) from e


def _load_yaml_raw_optional(path: Path) -> Any | None:
    """Load raw YAML optionally. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    return _load_yaml_raw(path)


# ---------------------------------------------------------------------------
# Rules config loader (with _from: pointer resolution)
# ---------------------------------------------------------------------------

def _load_rules_config(rules_path: Path, pack_dir: Path) -> RulesConfig:
    """Load and resolve rules.yaml, honoring _from: pointers on confrontation
    interaction_table fields.

    Port of Rust load_rules_config().

    A confrontation may carry its interaction table inline or reference a
    sibling file pack-relative:
        interaction_table:
          _from: dogfight/interactions_mvp.yaml

    The resolver substitutes _from pointers, rejects absolute paths and
    parent-directory traversal, and rejects nested _from chains.

    Raises:
        GenreLoadError: If the file or any referenced file cannot be read.
    """
    try:
        text = rules_path.read_text(encoding="utf-8")
    except OSError as e:
        raise GenreLoadError(path=rules_path, detail=str(e)) from e

    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise GenreLoadError(path=rules_path, detail=str(e)) from e

    # Walk confrontations[].interaction_table for _from pointers
    if isinstance(value, dict) and "confrontations" in value:
        confrontations = value["confrontations"]
        if isinstance(confrontations, list):
            for conf in confrontations:
                _resolve_confrontation_from_pointers(conf, pack_dir)

    try:
        return RulesConfig.model_validate(value)
    except Exception as e:
        raise GenreLoadError(path=rules_path, detail=str(e)) from e


def _resolve_confrontation_from_pointers(conf: Any, pack_dir: Path) -> None:
    """Walk a single confrontation dict and resolve any _from pointers on its
    interaction_table field. Mutates conf in place.

    Port of Rust resolve_confrontation_from_pointers().
    """
    if not isinstance(conf, dict):
        return
    it_value = conf.get("interaction_table")
    if it_value is None:
        return
    from_rel = _extract_from_pointer(it_value)
    if from_rel is None:
        return
    resolved = _resolve_from_pointer(from_rel, pack_dir)
    conf["interaction_table"] = resolved


def _extract_from_pointer(value: Any) -> str | None:
    """If value is a mapping of shape { _from: "relpath" } (single key), return the string.

    Port of Rust extract_from_pointer().
    """
    if not isinstance(value, dict) or len(value) != 1:
        return None
    return value.get("_from")


def _resolve_from_pointer(rel: str, pack_dir: Path) -> Any:
    """Read a _from-referenced sub-file, enforcing pack-relative path safety
    and rejecting nested _from chains.

    Port of Rust resolve_from_pointer().

    Raises:
        GenreLoadError: If the path is absolute, contains .., or the sub-file
            contains a nested _from pointer.
    """
    rel_path = Path(rel)

    if rel_path.is_absolute():
        raise GenreLoadError(
            path=rel,
            detail=f"_from path must be pack-relative (got absolute path: {rel})",
        )

    # Reject parent-directory traversal
    parts = rel_path.parts
    for part in parts:
        if part == "..":
            raise GenreLoadError(
                path=rel,
                detail=f"_from path must not contain parent-directory traversal: {rel}",
            )

    full = pack_dir / rel_path
    try:
        text = full.read_text(encoding="utf-8")
    except OSError as e:
        raise GenreLoadError(path=full, detail=str(e)) from e

    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise GenreLoadError(path=full, detail=str(e)) from e

    # Reject nested _from chains
    if isinstance(value, dict) and "_from" in value:
        raise GenreLoadError(
            path=full,
            detail="nested _from pointers are not allowed",
        )

    return value


# ---------------------------------------------------------------------------
# Legends loader (flexible format)
# ---------------------------------------------------------------------------

def _load_legends_flexible(path: Path) -> tuple[list[Legend], Any]:
    """Load legends.yaml flexibly: accepts Vec<Legend> or a map with a "legends" key.

    Port of Rust load_legends_flexible().

    Returns:
        (legends, legends_raw) where legends_raw is the full raw value if the
        map format was used, else None.
    """
    if not path.exists():
        return [], None

    raw = _load_yaml_raw(path)

    # Try as list of Legend (low_fantasy format)
    if isinstance(raw, list):
        try:
            legends = [Legend.model_validate(item) for item in raw]
            return legends, None
        except Exception:
            pass

    # Try as a map — extract "legends" key if present, keep full raw value
    if isinstance(raw, dict):
        legends_raw_value: Any = raw
        if "legends" in raw:
            try:
                legends = [Legend.model_validate(item) for item in raw["legends"]]
                return legends, legends_raw_value
            except Exception as e:
                raise GenreLoadError(path=path, detail=str(e)) from e
        return [], legends_raw_value

    raise GenreLoadError(path=path, detail="unrecognized legends.yaml format")


# ---------------------------------------------------------------------------
# Subdirectory loader
# ---------------------------------------------------------------------------

def _load_subdirectories(
    pack_path: Path,
    subdir: str,
    loader: Any,
) -> dict[str, Any]:
    """Load all subdirectories of {pack_path}/{subdir}/ into a dict.

    Port of Rust load_subdirectories().
    """
    dir_path = pack_path / subdir
    if not dir_path.exists():
        return {}

    result: dict[str, Any] = {}
    try:
        entries = sorted(dir_path.iterdir())
    except OSError as e:
        raise GenreLoadError(path=dir_path, detail=str(e)) from e

    for entry in entries:
        if entry.is_dir():
            slug = entry.name
            item = loader(entry)
            result[slug] = item

    return result


# ---------------------------------------------------------------------------
# World loader
# ---------------------------------------------------------------------------

def _load_single_world(world_path: Path, genre_tropes: list[TropeDefinition]) -> World:
    """Load a single world from its directory.

    Port of Rust load_single_world().

    Raises:
        GenreLoadError: If required files are missing or malformed.
    """
    config: WorldConfig = _load_yaml(world_path / "world.yaml", WorldConfig)
    lore: WorldLore = _load_yaml(world_path / "lore.yaml", WorldLore)
    cartography: CartographyConfig = _load_yaml(world_path / "cartography.yaml", CartographyConfig)

    # When navigation_mode is RoomGraph, load rooms from a separate rooms.yaml file
    if cartography.navigation_mode == NavigationMode.room_graph:
        rooms_raw = _load_yaml_raw_optional(world_path / "rooms.yaml")
        if rooms_raw is not None:
            from sidequest.genre.models.world import RoomDef
            rooms = [RoomDef.model_validate(r) for r in rooms_raw] if isinstance(rooms_raw, list) else None
            # Pydantic model is frozen — build a new one with rooms set
            cartography = cartography.model_copy(update={"rooms": rooms})

    cultures_raw = _load_yaml_raw_optional(world_path / "cultures.yaml")
    cultures: list[Culture] = (
        [Culture.model_validate(c) for c in cultures_raw]
        if isinstance(cultures_raw, list)
        else []
    )

    # Legends: accept either Vec<Legend> (low_fantasy) or map with "legends" key (road_warrior).
    legends, legends_raw = _load_legends_flexible(world_path / "legends.yaml")

    # Load world tropes and resolve inheritance from genre-level tropes
    world_tropes_raw = _load_yaml_raw_optional(world_path / "tropes.yaml")
    raw_world_tropes: list[TropeDefinition] = (
        [TropeDefinition.model_validate(t) for t in world_tropes_raw]
        if isinstance(world_tropes_raw, list)
        else []
    )
    tropes = (
        resolve_trope_inheritance(genre_tropes, raw_world_tropes) if raw_world_tropes else []
    )

    # Optional world-level overrides
    archetypes_raw = _load_yaml_raw_optional(world_path / "archetypes.yaml")
    archetypes: list[NpcArchetype] = (
        [NpcArchetype.model_validate(a) for a in archetypes_raw]
        if isinstance(archetypes_raw, list)
        else []
    )

    visual_style: Any = _load_yaml_raw_optional(world_path / "visual_style.yaml")
    history: Any = _load_yaml_raw_optional(world_path / "history.yaml")

    archetype_funnels: ArchetypeFunnels | None = _load_yaml_optional(
        world_path / "archetype_funnels.yaml", ArchetypeFunnels
    )

    # World-tier opening hooks and chargen scenes
    openings_raw = _load_yaml_raw_optional(world_path / "openings.yaml")
    openings: list[OpeningHook] = (
        [OpeningHook.model_validate(o) for o in openings_raw]
        if isinstance(openings_raw, list)
        else []
    )

    # World-tier multiplayer openings — see worlds/{slug}/mp_opening.yaml
    # for the canonical shape (e.g., coyote_star/mp_opening.yaml). The
    # file's top-level `mp_openings:` list is the source of truth.
    mp_openings_raw = _load_yaml_raw_optional(world_path / "mp_opening.yaml")
    mp_openings: list[MpOpening] = []
    if isinstance(mp_openings_raw, dict):
        entries = mp_openings_raw.get("mp_openings")
        if isinstance(entries, list):
            mp_openings = [MpOpening.model_validate(o) for o in entries]

    char_creation_raw = _load_yaml_raw_optional(world_path / "char_creation.yaml")
    char_creation: list[CharCreationScene] = (
        [CharCreationScene.model_validate(c) for c in char_creation_raw]
        if isinstance(char_creation_raw, list)
        else []
    )

    # Portrait manifest
    portrait_raw = _load_yaml_raw_optional(world_path / "portrait_manifest.yaml")
    portrait_manifest: list[PortraitManifestEntry] = []
    if isinstance(portrait_raw, dict) and "characters" in portrait_raw:
        portrait_manifest = [
            PortraitManifestEntry.model_validate(e)
            for e in portrait_raw["characters"]
            if isinstance(portrait_raw["characters"], list)
        ]
    elif isinstance(portrait_raw, list):
        portrait_manifest = [PortraitManifestEntry.model_validate(e) for e in portrait_raw]

    return World(
        config=config,
        lore=lore,
        legends=legends,
        cartography=cartography,
        cultures=cultures,
        tropes=tropes,
        archetypes=archetypes,
        visual_style=visual_style,
        history=history,
        legends_raw=legends_raw,
        portrait_manifest=portrait_manifest,
        archetype_funnels=archetype_funnels,
        openings=openings,
        mp_openings=mp_openings,
        char_creation=char_creation,
    )


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------

def _load_single_scenario(scenario_path: Path) -> ScenarioPack:
    """Load a single scenario from its directory.

    Port of Rust load_single_scenario().
    """
    scenario: ScenarioPack = _load_yaml(scenario_path / "scenario.yaml", ScenarioPack)

    # Overlay supplementary files
    matrix_raw = _load_yaml_raw_optional(scenario_path / "assignment_matrix.yaml")
    if matrix_raw is not None:
        from sidequest.genre.models.scenario import AssignmentMatrix
        scenario = scenario.model_copy(update={"assignment_matrix": AssignmentMatrix.model_validate(matrix_raw)})

    graph_raw = _load_yaml_raw_optional(scenario_path / "clue_graph.yaml")
    if graph_raw is not None:
        from sidequest.genre.models.scenario import ClueGraph
        scenario = scenario.model_copy(update={"clue_graph": ClueGraph.model_validate(graph_raw)})

    atmo_raw = _load_yaml_raw_optional(scenario_path / "atmosphere_matrix.yaml")
    if atmo_raw is not None:
        from sidequest.genre.models.scenario import AtmosphereMatrix
        scenario = scenario.model_copy(update={"atmosphere_matrix": AtmosphereMatrix.model_validate(atmo_raw)})

    npcs_raw = _load_yaml_raw_optional(scenario_path / "npcs.yaml")
    if npcs_raw is not None and isinstance(npcs_raw, list):
        npcs = [ScenarioNpc.model_validate(n) for n in npcs_raw]
        scenario = scenario.model_copy(update={"npcs": npcs})

    return scenario


# ---------------------------------------------------------------------------
# Top-level pack loader
# ---------------------------------------------------------------------------

def load_genre_pack(path: Path | str) -> GenrePack:
    """Load a complete genre pack from a directory.

    Reads all YAML files, loads worlds and scenarios, resolves trope inheritance,
    and returns a fully assembled GenrePack.

    Port of Rust load_genre_pack(path).

    Args:
        path: Path to the genre pack directory (e.g. `.../genre_packs/caverns_and_claudes`).

    Returns:
        A fully assembled GenrePack.

    Raises:
        GenreLoadError: If any required file is missing, unreadable, or malformed.
    """
    path = Path(path)

    if not path.exists() or not path.is_dir():
        raise GenreLoadError(
            path=path,
            detail="directory does not exist",
        )

    # Load required files
    meta = _load_yaml(path / "pack.yaml", PackMeta)
    rules = _load_rules_config(path / "rules.yaml", path)
    lore = _load_yaml(path / "lore.yaml", Lore)
    theme = _load_yaml(path / "theme.yaml", GenreTheme)
    archetypes_raw = _load_yaml_raw(path / "archetypes.yaml")
    archetypes: list[NpcArchetype] = [NpcArchetype.model_validate(a) for a in archetypes_raw] if isinstance(archetypes_raw, list) else []
    char_creation_raw = _load_yaml_raw(path / "char_creation.yaml")
    char_creation: list[CharCreationScene] = [CharCreationScene.model_validate(c) for c in char_creation_raw] if isinstance(char_creation_raw, list) else []
    visual_style = _load_yaml(path / "visual_style.yaml", VisualStyle)
    progression = _load_yaml(path / "progression.yaml", ProgressionConfig)
    axes = _load_yaml(path / "axes.yaml", AxesConfig)
    audio = _load_yaml(path / "audio.yaml", AudioConfig)
    cultures_raw = _load_yaml_raw(path / "cultures.yaml")
    cultures: list[Culture] = [Culture.model_validate(c) for c in cultures_raw] if isinstance(cultures_raw, list) else []
    prompts = _load_yaml(path / "prompts.yaml", Prompts)

    # Load required genre-level tropes
    genre_tropes_raw = _load_yaml_raw(path / "tropes.yaml")
    genre_tropes: list[TropeDefinition] = [TropeDefinition.model_validate(t) for t in genre_tropes_raw] if isinstance(genre_tropes_raw, list) else []

    # Load optional files
    achievements_raw = _load_yaml_raw_optional(path / "achievements.yaml")
    achievements: list[Achievement] = (
        [Achievement.model_validate(a) for a in achievements_raw]
        if isinstance(achievements_raw, list)
        else []
    )

    power_tiers_raw = _load_yaml_raw_optional(path / "power_tiers.yaml")
    power_tiers: dict[str, list[PowerTier]] = {}
    if isinstance(power_tiers_raw, dict):
        for k, v in power_tiers_raw.items():
            if isinstance(v, list):
                power_tiers[k] = [PowerTier.model_validate(pt) for pt in v]

    beat_vocabulary: BeatVocabulary | None = _load_yaml_optional(
        path / "beat_vocabulary.yaml", BeatVocabulary
    )
    chassis_classes: ChassisClassesConfig | None = _load_yaml_optional(
        path / "chassis_classes.yaml", ChassisClassesConfig
    )
    voice_presets: VoicePresets | None = _load_yaml_optional(
        path / "voice_presets.yaml", VoicePresets
    )
    drama_thresholds: DramaThresholds | None = _load_yaml_optional(
        path / "pacing.yaml", DramaThresholds
    )
    inventory: InventoryConfig | None = _load_yaml_optional(
        path / "inventory.yaml", InventoryConfig
    )

    openings_raw = _load_yaml_raw_optional(path / "openings.yaml")
    openings: list[OpeningHook] = (
        [OpeningHook.model_validate(o) for o in openings_raw]
        if isinstance(openings_raw, list)
        else []
    )

    backstory_tables: BackstoryTables | None = _load_yaml_optional(
        path / "backstory_tables.yaml", BackstoryTables
    )
    equipment_tables: EquipmentTables | None = _load_yaml_optional(
        path / "equipment_tables.yaml", EquipmentTables
    )

    archetype_constraints: ArchetypeConstraints | None = _load_yaml_optional(
        path / "archetype_constraints.yaml", ArchetypeConstraints
    )

    # Base archetypes and npc_traits live at content root (parent of genre_packs/)
    content_root: Path | None = None
    parent = path.parent  # genre_packs/
    if parent.exists():
        grandparent = parent.parent  # content root
        if grandparent.exists():
            content_root = grandparent

    base_archetypes: BaseArchetypes | None = None
    npc_traits: NpcTraitsDatabase | None = None
    if content_root is not None:
        base_archetypes = _load_yaml_optional(
            content_root / "archetypes_base.yaml", BaseArchetypes
        )
        npc_traits = _load_yaml_optional(
            content_root / "npc_traits.yaml", NpcTraitsDatabase
        )

    # Load worlds and scenarios from subdirectories
    worlds: dict[str, World] = _load_subdirectories(
        path, "worlds", lambda p: _load_single_world(p, genre_tropes)
    )
    scenarios: dict[str, ScenarioPack] = _load_subdirectories(
        path, "scenarios", _load_single_scenario
    )

    # Task 22: load optional projection.yaml.
    projection_yaml = path / "projection.yaml"
    projection_rules = None
    if projection_yaml.exists():
        from sidequest.game.projection.rules import load_rules_from_yaml_path
        from sidequest.game.projection.validator import validate_projection_rules

        projection_rules = load_rules_from_yaml_path(projection_yaml)
        validate_projection_rules(projection_rules)  # raises on error — no silent fallback

    # Group G Task 2: required visibility baseline — decomposer reads this at
    # session init. No silent fallback: missing file is a pack-authoring bug.
    from sidequest.genre.models.visibility import load_baseline

    visibility_baseline_path = path / "visibility_baseline.yaml"
    try:
        visibility_baseline = load_baseline(visibility_baseline_path)
    except FileNotFoundError as e:
        raise GenreLoadError(path=visibility_baseline_path, detail=str(e)) from e
    except Exception as e:
        raise GenreLoadError(path=visibility_baseline_path, detail=str(e)) from e

    # Group C Task 4: required lethality policy — LethalityArbiter reads this
    # at every turn. No silent fallback: missing file is a pack-authoring bug,
    # same pattern as visibility_baseline above.
    from sidequest.genre.lethality_policy_loader import (
        LethalityPolicyMissingError,
        load_lethality_policy,
    )

    try:
        lethality_policy = load_lethality_policy(path)
    except LethalityPolicyMissingError as e:
        raise GenreLoadError(path=path / "lethality_policy.yaml", detail=str(e)) from e
    except Exception as e:
        raise GenreLoadError(path=path / "lethality_policy.yaml", detail=str(e)) from e

    return GenrePack(
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
        tropes=genre_tropes,
        beat_vocabulary=beat_vocabulary,
        chassis_classes=chassis_classes,
        achievements=achievements,
        voice_presets=voice_presets,
        power_tiers=power_tiers,
        worlds=worlds,
        scenarios=scenarios,
        drama_thresholds=drama_thresholds,
        inventory=inventory,
        openings=openings,
        backstory_tables=backstory_tables,
        equipment_tables=equipment_tables,
        base_archetypes=base_archetypes,
        archetype_constraints=archetype_constraints,
        npc_traits=npc_traits,
        projection_rules=projection_rules,
        visibility_baseline=visibility_baseline,
        lethality_policy=lethality_policy,
        source_dir=path,
    )


# ---------------------------------------------------------------------------
# GenreLoader — multi-path search class
# ---------------------------------------------------------------------------

class GenreLoader:
    """Multi-path genre pack loader.

    Searches a list of directories in order for genre packs, loading the first
    match found. Supports the search order: local, home, install.

    Port of Rust GenreLoader struct (loader.rs).
    """

    def __init__(self, search_paths: list[Path] | None = None) -> None:
        """Create a loader with the given search paths (checked in order).

        If search_paths is None, uses DEFAULT_GENRE_PACK_SEARCH_PATHS.
        """
        self.search_paths: list[Path] = (
            search_paths if search_paths is not None else DEFAULT_GENRE_PACK_SEARCH_PATHS
        )

    def find(self, code: str | GenreCode) -> Path:
        """Find the directory for a genre code by searching all paths.

        Returns the first path where {search_path}/{genre_code}/ exists as a directory.

        Port of Rust GenreLoader::find().

        Raises:
            GenreNotFoundError: If not found in any search path.
        """
        code_str = str(code)
        searched: list[str] = []
        for base in self.search_paths:
            candidate = base / code_str
            if candidate.is_dir():
                return candidate
            searched.append(str(base))
        raise GenreNotFoundError(code=code_str, searched=searched)

    def load(self, code: str | GenreCode) -> GenrePack:
        """Find and load a genre pack by code.

        Port of Rust GenreLoader::load().

        Raises:
            GenreNotFoundError: If the pack directory is not found.
            GenreLoadError: If loading fails.
        """
        path = self.find(code)
        return load_genre_pack(path)


def find_pack_dir(code: str | GenreCode, search_paths: list[Path]) -> Path:
    """Find the pack directory for a genre code. Returns the first match.

    Raises:
        GenreNotFoundError: If not found in any search path.
    """
    return GenreLoader(search_paths=search_paths).find(code)


# ---------------------------------------------------------------------------
# Cached loader
# ---------------------------------------------------------------------------

_default_cache: GenreCache | None = None


def _get_default_cache() -> GenreCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = GenreCache()
    return _default_cache


def clear_default_cache() -> None:
    """Evict all entries from the default process-lifetime cache.

    Exposed for test isolation; not present in the Rust original.
    """
    _get_default_cache().clear()


def load_genre_pack_cached(
    genre_code: str | GenreCode,
    search_paths: list[Path] | None = None,
) -> GenrePack:
    """As load_genre_pack but with process-lifetime caching.

    Args:
        genre_code: Genre code string or GenreCode.
        search_paths: Search paths (uses defaults if None).

    Returns:
        GenrePack — same object returned on repeated calls for the same code.
    """
    code_str = str(genre_code)
    loader = GenreLoader(search_paths=search_paths)
    return _get_default_cache().get_or_load(code_str, loader)
