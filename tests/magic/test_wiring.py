"""Phase 1 wiring/integration: production-path reachability + plugin completeness."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_magic_module_importable_from_top_level():
    """Production code can `from sidequest.magic import ...` cleanly."""
    from sidequest.magic import (  # noqa: F401
        ApplyWorkingResult,
        BarKey,
        Flag,
        FlagSeverity,
        LedgerBar,
        MagicState,
        MagicWorking,
        Plugin,
        ThresholdCrossingEvent,
        WorkingRecord,
        WorldMagicConfig,
    )


def test_plugin_registry_has_innate_and_item_legacy():
    """Importing the plugins package registers exactly the v1 set."""
    import sidequest.magic.plugins  # noqa: F401
    from sidequest.magic.plugin import MAGIC_PLUGINS

    assert set(MAGIC_PLUGINS) == {"innate_v1", "item_legacy_v1", "learned_v1"}


def test_every_plugin_py_file_registers_in_magic_plugins():
    """Completeness lint: every plugin .py file in plugins/ has an entry in MAGIC_PLUGINS.

    Mirrors tests/telemetry/test_routing_completeness.py for SPAN_ROUTES. If a
    new plugin .py file is added but not star-imported in plugins/__init__.py
    (or registers under a different id than its filename), this test fails
    loud at import time.
    """
    import sidequest.magic.plugins as plugins_pkg  # noqa: F401  # populate MAGIC_PLUGINS
    from sidequest.magic.plugin import MAGIC_PLUGINS

    plugins_dir = Path(plugins_pkg.__file__).parent
    py_files = {p.stem for p in plugins_dir.glob("*.py") if p.stem != "__init__"}
    assert py_files == set(MAGIC_PLUGINS), (
        f"plugin file/registry mismatch — files: {sorted(py_files)}, "
        f"registered: {sorted(MAGIC_PLUGINS)}. Each .py file must register "
        f"under a plugin_id matching its filename, and each must be star-"
        f"imported in plugins/__init__.py."
    )


def test_every_plugin_has_yaml_pair():
    """No plugin .py without a paired .yaml."""
    from sidequest.magic import plugins as plugins_pkg

    plugins_dir = Path(plugins_pkg.__file__).parent
    py_files = {p.stem for p in plugins_dir.glob("*.py") if p.stem != "__init__"}
    yaml_files = {p.stem for p in plugins_dir.glob("*.yaml")}
    assert py_files == yaml_files, (
        f"plugin .py and .yaml mismatch: only_py={py_files - yaml_files}, "
        f"only_yaml={yaml_files - py_files}"
    )


def test_loader_reachable_from_genre():
    """The genre.magic_loader is importable from where genre.loader will call it."""
    from sidequest.genre.magic_loader import LoaderError, load_world_magic  # noqa: F401


def test_validator_reachable_from_top_level():
    from sidequest.magic.validator import validate  # noqa: F401


def test_production_content_loads():
    """The actual production yamls in sidequest-content load cleanly."""
    import os

    from sidequest.genre.magic_loader import load_world_magic

    content_root = os.environ.get("SIDEQUEST_GENRE_PACKS")
    if not content_root:
        pytest.skip("SIDEQUEST_GENRE_PACKS not set")

    genre_yaml = Path(content_root) / "space_opera" / "magic.yaml"
    world_yaml = Path(content_root) / "space_opera" / "worlds" / "coyote_star" / "magic.yaml"
    if not (genre_yaml.exists() and world_yaml.exists()):
        pytest.skip("production magic yamls not present")

    config = load_world_magic(genre_yaml=genre_yaml, world_yaml=world_yaml)
    assert config.world_slug == "coyote_star"
    assert "innate_v1" in config.active_plugins
    assert "item_legacy_v1" in config.active_plugins
    assert config.intensity == 0.25


def test_magic_init_seeds_learned_v1_for_mage_class(tmp_path):
    """Mage chargen → init wires per-level slot bars + chosen known spells."""
    pytest.skip("integration test — see test_e2e_learned_v1.py for end-to-end")


def test_seed_learned_v1_state_instantiates_slot_bars_per_level():
    from sidequest.genre.models.character import ClassDef, ClassMagicConfig
    from sidequest.magic.models import WorldKnowledge, WorldMagicConfig
    from sidequest.magic.state import BarKey, MagicState
    from sidequest.server.magic_init import seed_learned_v1_state

    class_def = ClassDef(
        id="mage",
        display_name="Mage",
        rpg_role="control",
        jungian_default="magician",
        prime_requisite="INT",
        minimum_score=9,
        kit_table="mage_kit",
        magic_access="learned_v1",
        magic_config=ClassMagicConfig(
            tradition="arcane",
            slots_by_class_level={"1": {"1": 1}, "3": {"1": 2, "2": 1}},
            starting_known_spells=2,
            save_dc_stat="INT",
        ),
    )
    state = MagicState.from_config(
        WorldMagicConfig(
            world_slug="test",
            genre_slug="test_genre",
            allowed_sources=["learned"],
            active_plugins=["learned_v1"],
            intensity=0.5,
            world_knowledge=WorldKnowledge(primary="folkloric"),
            visibility={"primary": "feared"},
            ledger_bars=[],
            hard_limits=[],
            cost_types=[],
            narrator_register="standard",
        )
    )
    state.add_character("rux")

    seed_learned_v1_state(
        state,
        actor="rux",
        class_def=class_def,
        class_level=1,
        chosen_known_spells=["magic_missile", "sleep"],
    )

    assert state.known_spells["rux"] == ["magic_missile", "sleep"]
    bar = state.get_bar(BarKey(scope="character", owner_id="rux", bar_id="slots_l1"))
    assert bar.value == 1.0
    assert bar.spec.range == (0.0, 1.0)
