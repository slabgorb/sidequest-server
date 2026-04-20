"""Lore types from lore.yaml.

Port of sidequest-genre/src/models/lore.rs.
Note: Lore uses flatten extras — no extra="forbid".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Faction(BaseModel):
    """A political or social faction."""

    # No extra="forbid": flatten extras bag.
    name: str
    summary: str
    description: str
    disposition: str = ""

    # Capture genre-specific extensions via model_config extra=allow
    model_config = {"extra": "allow"}


class Lore(BaseModel):
    """Genre-level lore."""

    # No extra="forbid": flatten extras bag (setting_anchor, themes, etc.)
    model_config = {"extra": "allow"}

    world_name: str
    history: str
    geography: str
    cosmology: str
    factions: list[Faction] = Field(default_factory=list)


class WorldLore(BaseModel):
    """World-specific lore. Accepts both low_fantasy and road_warrior formats."""

    # No extra="forbid": flatten extras bag.
    model_config = {"extra": "allow"}

    world_name: str | None = None
    history: str | None = None
    geography: str | None = None
    cosmology: str | None = None
    factions: list[Faction] = Field(default_factory=list)
