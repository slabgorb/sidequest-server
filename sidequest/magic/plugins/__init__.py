"""Plugin package — star-imports each submodule so MAGIC_PLUGINS is populated.

Mirrors sidequest/telemetry/spans/__init__.py star-import-of-domain-modules
pattern. Each plugin submodule mutates MAGIC_PLUGINS in place; importing this
package triggers all the mutations.
"""
from sidequest.magic.plugins.innate_v1 import *  # noqa: F401, F403
