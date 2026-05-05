"""Hub-world persistence — survives ``SqliteStore.init_session()`` reinit.

This is the second tier of persistence: session_meta carries identity,
game_state carries the per-delve snapshot (cleared on reinit), and
world_save carries hub-shaped data that lives across delves into
different dungeons within one campaign.

Engine plan item 2 of the Hamlet-of-Sünden spec. Only the data layer
ships here. Mechanics that mutate these fields land in items 3 and 4
of the spec (see file header for plan links).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Hireling(BaseModel):
    """A roster member. Lives in WorldSave; materialized into
    GameSnapshot.characters at delve-start (item 4).

    The `stress` field is declared here for storage but its accrual
    mechanics are item 3 of the spec. This plan never increments it.
    """

    model_config = {"extra": "ignore"}

    # Stable identifier — slug-shaped, lowercase, alphanumeric + underscore.
    # The pattern is enforced so item 4a's recruit generator and items
    # 5/6/7's narrator-zone consumers cannot drift on shape (e.g. one
    # picks UUIDs, the other expects vol_1). The recruit generator owns
    # the construction; this field locks the contract.
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    name: str                                # display name
    archetype: str                           # archetype slug from world archetypes.yaml
    stress: int = 0                          # 0..100 (item 3 enforces bounds)
    status: Literal["active", "dead", "missing"] = "active"
    recruited_at_delve: int = 0              # the WorldSave.delve_count when added
    notes: str = ""                          # narrator-emitted flavor; free text
