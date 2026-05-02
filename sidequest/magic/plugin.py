"""MagicPlugin Protocol + bare module-level MAGIC_PLUGINS registry.

Plugins are paired files: a .py module (mechanics) and a .yaml file (content).
Each plugin module assigns its instance to MAGIC_PLUGINS[plugin_id] at module
import time. The package __init__.py star-imports each plugin module so the
side-effect mutation fires for every shipped plugin.

This mirrors the codebase's house pattern in sidequest/telemetry/spans/_core.py
where SPAN_ROUTES is mutated by domain submodules at import. Renames break at
import time; tests/magic/test_plugin_registry.py enforces completeness in the
same shape tests/telemetry/test_routing_completeness.py does for spans.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sidequest.magic.models import Flag, MagicWorking, WorldMagicConfig


@runtime_checkable
class MagicPlugin(Protocol):
    """Protocol every plugin module class must implement."""

    plugin_id: str

    def required_attrs(self) -> set[str]:
        """Plugin-specific MagicWorking fields that MUST be populated."""

    def validate_working(self, working: MagicWorking, config: WorldMagicConfig) -> list[Flag]:
        """Plugin-side validation — yellow/red/deep_red flags. Empty list = clean."""


# Plugin id -> MagicPlugin instance. Each plugin submodule mutates this in
# place at import time. The package __init__.py star-imports each plugin
# module so the side effect fires for every shipped plugin.
MAGIC_PLUGINS: dict[str, MagicPlugin] = {}


def get_plugin(plugin_id: str) -> MagicPlugin:
    """Lookup helper that raises a useful KeyError listing what IS registered."""
    try:
        return MAGIC_PLUGINS[plugin_id]
    except KeyError as e:
        raise KeyError(
            f"plugin {plugin_id!r} is not registered; registered plugins: {sorted(MAGIC_PLUGINS)}"
        ) from e
