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

from datetime import datetime
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


class WallEntry(BaseModel):
    """One row of Sünden's Wall (the campaign-memory monument).

    Append-only ledger; the Wall does not erase. One entry per
    delve-resolution event, regardless of outcome.
    """

    model_config = {"extra": "ignore"}

    delve_number: int                        # 1-indexed, matches WorldSave.delve_count at write-time
    sin: str                                 # the dungeon's sin slug ("pride" | "greed" | "gluttony"); read from Dungeon.config.sin at write-time
    dungeon: str                             # dungeon slug ("grimvault" | "horden" | "mawdeep")
    party_hireling_ids: list[str]            # ids of the hirelings who delved (alive or dead)

    # Party fate. Three orthogonal-to-wound outcomes: cleared dungeon
    # without TPK (victory), TPK (defeat), or chose to leave alive
    # (retreat). The spec ("Wounded Sins") treats wound-status as a
    # SEPARATE flag — see ``wounded_boss`` below. This split lets
    # "TPK after wounding the boss" be recorded honestly instead of
    # being forced into a single conflated literal.
    outcome: Literal["victory", "defeat", "retreat"]

    # Did this delve culminate in the boss-floor / wound event the
    # spec calls out? Independent of ``outcome`` — a defeat can still
    # have wounded the boss. The post-delve apply step uses this flag
    # to flip ``WorldSave.dungeon_wounds[dungeon]``.
    wounded_boss: bool = False

    timestamp: datetime                      # write-time, UTC


class WorldSave(BaseModel):
    """Hub-world state that persists across delves.

    Each campaign (.db file) has at most one WorldSave row. Fresh hub
    worlds get a default-populated WorldSave on first read. Non-hub
    worlds never instantiate one (no production code reads them).
    """

    model_config = {"extra": "ignore"}

    roster: list[Hireling] = Field(default_factory=list)
    currency: int = 0
    wall: list[WallEntry] = Field(default_factory=list)

    # Per-dungeon wound flag. Keys are dungeon slugs from the genre pack;
    # absence means "not yet wounded". Item 4a flips the bool to True on
    # any delve-end where ``WallEntry.wounded_boss`` is True (regardless
    # of outcome — TPK-after-wound still wounds the dungeon). Once True,
    # never flips back (spec §"Wounded Sins": "A dungeon can only be
    # wounded once"). Item 6 reads the flag to merge wound_profile.yaml
    # into the Keeper definition.
    dungeon_wounds: dict[str, bool] = Field(default_factory=dict)

    # The most-recent-delve drift flag. None on a campaign with no
    # completed delves. Set by item 4 at delve-end; consumed by item 5
    # in the Hamlet-scene prompt zone. Overwritten on every subsequent
    # delve — the spec deliberately limits drift to the most recent.
    latest_delve_sin: str | None = None

    # Monotonic counter of completed delves (any outcome). Used as the
    # delve_number stamp for WallEntry and as the time-axis for any
    # future "recruited at delve N" UI affordances.
    delve_count: int = 0

    # ISO-8601 string set by save_world_save(); read for the GM panel.
    last_saved_at: datetime | None = None
