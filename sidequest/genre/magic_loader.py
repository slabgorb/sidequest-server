"""Magic config loader — yaml → WorldMagicConfig.

Composes a genre-layer yaml with a world-layer yaml. World values
override genre defaults; world activates a subset of genre-permitted
plugins. Loader fails loud per project no-silent-fallback rule.

**Composition order (per architect addendum 2026-04-29 §5.1):**

  1. Plugin descriptor `.yaml` provides defaults — narrator_register,
     ledger_bar_templates, output_catalog descriptions. The genre-neutral
     plugin voice.
  2. Genre `magic.yaml` MAY override per-field — genre-flavored plugin voice.
  3. World `magic.yaml` MAY override per-field — world-flavored plugin voice.

Last-writer-wins per field. The composer walks fields independently;
overriding `narrator_register` does not also override `ledger_bar_templates`.
The active_plugins/permitted_plugins/allowed_sources gates are NOT
overridable — the world MUST be a strict subset of what the genre permits.

**This loader implements layers (2) and (3) only.** Plugin-default
fallback (layer 1) is the consumer's responsibility — the narrator
context builder (Task 3.1) imports the active plugin descriptors and
composes their `narrator_register` with the value this loader returns.
The split keeps the loader free of any plugin-registry dependency and
makes the composition order observable at the call site that actually
consumes it.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from sidequest.genre.error import GenreError
from sidequest.magic.models import (
    HardLimit,
    LedgerBarSpec,
    WorldKnowledge,
    WorldMagicConfig,
)


class LoaderError(GenreError):
    """Raised when magic-config loading fails for any reason.

    Subclasses ``GenreError`` so callers catching the genre-module exception
    family also catch magic-loader failures (mirrors the existing
    ``GenreLoadError`` / ``SchemaValidationError`` pattern in
    ``sidequest/genre/error.py``).
    """


def load_world_magic(*, genre_yaml: Path, world_yaml: Path) -> WorldMagicConfig:
    """Load and compose genre + world magic yamls into a WorldMagicConfig.

    Either path missing → LoaderError. Schema validation failures →
    LoaderError. Active-plugin not in genre permitted_plugins → LoaderError.
    """
    if not genre_yaml.exists():
        raise LoaderError(f"genre magic yaml not found: {genre_yaml}")
    if not world_yaml.exists():
        raise LoaderError(f"world magic yaml not found: {world_yaml}")

    try:
        genre_data = yaml.safe_load(genre_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise LoaderError(f"genre yaml parse error: {genre_yaml}: {e}") from e

    try:
        world_data = yaml.safe_load(world_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise LoaderError(f"world yaml parse error: {world_yaml}: {e}") from e

    # Active-plugin validation against genre's permitted_plugins
    permitted = set(genre_data.get("permitted_plugins", []))
    active = list(world_data.get("active_plugins", []))
    for ap in active:
        if ap not in permitted:
            raise LoaderError(
                f"active_plugin {ap!r} not in genre permitted_plugins {sorted(permitted)}"
            )

    # World-knowledge composition (world overrides genre default)
    wk_world = world_data.get("world_knowledge")
    wk_genre = genre_data.get("world_knowledge_default", {})
    wk_dict = wk_world if wk_world else wk_genre
    try:
        world_knowledge = WorldKnowledge.model_validate(wk_dict)
    except ValidationError as e:
        raise LoaderError(f"world_knowledge invalid: {e}") from e

    # Hard limits: genre union world_additional
    try:
        genre_limits = [HardLimit.model_validate(h) for h in genre_data.get("hard_limits", [])]
        world_extra = [
            HardLimit.model_validate(h) for h in world_data.get("hard_limits_additional", [])
        ]
    except ValidationError as e:
        raise LoaderError(f"hard_limits invalid: {e}") from e
    hard_limits = genre_limits + world_extra

    # Cost types: world's active subset (must subset genre's full set)
    genre_cost_types = set(genre_data.get("cost_types", []))
    world_cost_types = list(world_data.get("cost_types_active", genre_data.get("cost_types", [])))
    for ct in world_cost_types:
        if ct not in genre_cost_types:
            raise LoaderError(
                f"world cost_type {ct!r} not in genre cost_types {sorted(genre_cost_types)}"
            )

    # Ledger bars
    try:
        ledger_bars = [LedgerBarSpec.model_validate(b) for b in world_data.get("ledger_bars", [])]
    except ValidationError as e:
        raise LoaderError(f"ledger_bars invalid: {e}") from e

    # Intensity: world override or genre default
    intensity = world_data.get("intensity", genre_data.get("intensity", {}).get("default", 0.5))

    try:
        return WorldMagicConfig(
            world_slug=world_data["world"],
            genre_slug=world_data.get("genre", genre_data.get("genre")),
            allowed_sources=list(genre_data.get("allowed_sources", [])),
            active_plugins=active,
            intensity=intensity,
            world_knowledge=world_knowledge,
            visibility=world_data.get("visibility", {}),
            hard_limits=hard_limits,
            cost_types=world_cost_types,
            ledger_bars=ledger_bars,
            can_build_caster=world_data.get("can_build_caster", False),
            can_build_item_user=world_data.get("can_build_item_user", True),
            narrator_register=world_data.get(
                "narrator_register", genre_data.get("narrator_register", "")
            ),
        )
    except ValidationError as e:
        raise LoaderError(f"WorldMagicConfig schema error: {e}") from e
    except KeyError as e:
        raise LoaderError(f"required field missing: {e}") from e
