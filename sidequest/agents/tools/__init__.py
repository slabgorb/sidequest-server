"""Tool adapters — Phase C populates this package.

Each adapter module calls @tool at import time. This barrel imports each
adapter so the registry is loaded by importing this package.
"""

# Phase C will add lines like:
#   from sidequest.agents.tools import lookup_monster  # noqa: F401
# one per adapter, here.
from sidequest.agents.tools import (
    apply_damage,  # noqa: F401
    roll_dice,  # noqa: F401
)
