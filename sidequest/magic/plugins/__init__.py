"""Plugin package — star-imports each submodule so MAGIC_PLUGINS is populated.

Mirrors sidequest/telemetry/spans/__init__.py star-import-of-domain-modules
pattern. Each plugin submodule mutates MAGIC_PLUGINS in place at import; this
package's star-imports trigger all the mutations.
"""

from sidequest.magic.plugins.innate_v1 import *  # noqa: F401, F403
from sidequest.magic.plugins.item_legacy_v1 import *  # noqa: F401, F403
from sidequest.magic.plugins.learned_v1 import *  # noqa: F401, F403
