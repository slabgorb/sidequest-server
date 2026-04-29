"""Plugin Protocol + bare module-level MAGIC_PLUGINS dict.

Mirrors the SPAN_ROUTES pattern in sidequest/telemetry/spans/_core.py.
Each plugin module mutates MAGIC_PLUGINS at import time; the package
__init__.py star-imports each plugin module to trigger registration.
"""
from __future__ import annotations

import pytest

from sidequest.magic.models import Flag, FlagSeverity, MagicWorking, WorldMagicConfig
from sidequest.magic.plugin import MAGIC_PLUGINS, MagicPlugin, get_plugin


class _FakePlugin:
    plugin_id = "fake_v1"

    def required_attrs(self) -> set[str]:
        return {"flavor"}

    def validate_working(
        self, working: MagicWorking, config: WorldMagicConfig
    ) -> list[Flag]:
        if working.flavor is None:
            return [Flag(severity=FlagSeverity.RED, reason="missing_flavor")]
        return []


def test_magic_plugins_is_module_level_dict():
    """MAGIC_PLUGINS exists as a bare dict at module level."""
    assert isinstance(MAGIC_PLUGINS, dict)


def test_magic_plugin_protocol_runtime_checkable():
    """A class with the right shape is recognized as a MagicPlugin."""
    plugin = _FakePlugin()
    assert isinstance(plugin, MagicPlugin)


def test_register_via_dict_mutation_and_lookup():
    """Plugin modules register by direct dict mutation; lookup is dict access."""
    snapshot = dict(MAGIC_PLUGINS)
    try:
        plugin = _FakePlugin()
        MAGIC_PLUGINS[plugin.plugin_id] = plugin
        assert MAGIC_PLUGINS["fake_v1"].plugin_id == "fake_v1"
    finally:
        MAGIC_PLUGINS.clear()
        MAGIC_PLUGINS.update(snapshot)


def test_get_plugin_helper_raises_keyerror_with_registered_list():
    """get_plugin(id) helper raises KeyError listing what IS registered."""
    with pytest.raises(KeyError, match=r"registered plugins: \["):
        get_plugin("nonexistent_v1")
