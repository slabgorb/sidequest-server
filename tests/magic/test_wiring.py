"""Phase 1 wiring/integration: production-path reachability + plugin completeness."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_magic_module_importable_from_top_level():
    """Production code can `from sidequest.magic import ...` cleanly."""
    from sidequest.magic import (  # noqa: F401
        Flag,
        FlagSeverity,
        MagicWorking,
        Plugin,
        WorldMagicConfig,
    )


def test_plugin_registry_has_innate_and_item_legacy():
    """Importing the plugins package registers exactly the v1 set."""
    import sidequest.magic.plugins  # noqa: F401
    from sidequest.magic.plugin import MAGIC_PLUGINS

    assert set(MAGIC_PLUGINS) == {"innate_v1", "item_legacy_v1"}


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
    py_files = {
        p.stem
        for p in plugins_dir.glob("*.py")
        if p.stem != "__init__"
    }
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
    world_yaml = (
        Path(content_root) / "space_opera" / "worlds" / "coyote_reach" / "magic.yaml"
    )
    if not (genre_yaml.exists() and world_yaml.exists()):
        pytest.skip("production magic yamls not present")

    config = load_world_magic(genre_yaml=genre_yaml, world_yaml=world_yaml)
    assert config.world_slug == "coyote_reach"
    assert "innate_v1" in config.active_plugins
    assert "item_legacy_v1" in config.active_plugins
    assert config.intensity == 0.25
