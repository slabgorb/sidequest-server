"""Tool adapters — Phase C populates this package.

Each adapter module calls @tool at import time. This barrel imports each
adapter so the registry is loaded by importing this package.
"""

# Phase C will add lines like:
#   from sidequest.agents.tools import lookup_monster  # noqa: F401
# one per adapter, here.
from sidequest.agents.tools import (
    apply_damage,  # noqa: F401
    apply_status,  # noqa: F401
    commit_known_fact,  # noqa: F401
    list_npcs_in_scene,  # noqa: F401
    query_character,  # noqa: F401
    query_known_facts,  # noqa: F401
    query_npc,  # noqa: F401
    roll_dice,  # noqa: F401
    update_npc_disposition,  # noqa: F401
    update_resource_pool,  # noqa: F401
)
