"""Inventory and economy types from inventory.yaml.

Port of sidequest-genre/src/models/inventory.rs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CurrencyConfig(BaseModel):
    """Currency system definition."""

    model_config = {"extra": "forbid"}

    name: str
    denominations: Any = None  # accepts list[str] or dict[str, float]


class CatalogItem(BaseModel):
    """A single item in the genre pack's item catalog."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    description: str
    category: str
    value: int = 0
    weight: float = 0.0
    rarity: str = ""
    power_level: int = 0
    tags: list[str] = Field(default_factory=list)
    lore: str = ""
    narrative_weight: Any = None  # accepts string or numeric
    resource_ticks: int | None = None


class CarryMode(str, Enum):
    """Whether inventory limits are enforced by item count or total weight."""

    # Note: using 'item_count' as the enum name because 'count' conflicts with str.count().
    # The YAML value is "count" (matching the Rust snake_case rename).
    item_count = "count"
    weight = "weight"


class InventoryPhilosophy(BaseModel):
    """Inventory philosophy configuration."""

    model_config = {"extra": "forbid"}

    carry_limit: int | None = None
    carry_mode: CarryMode = CarryMode.item_count
    weight_limit: float | None = None
    restricted_categories: list[str] = Field(default_factory=list)
    progression_gates: dict[str, Any] = Field(default_factory=dict)


class InventoryConfig(BaseModel):
    """Complete inventory configuration from inventory.yaml."""

    model_config = {"extra": "forbid"}

    currency: CurrencyConfig | None = None
    item_catalog: list[CatalogItem] = Field(default_factory=list)
    starting_equipment: dict[str, list[str]] = Field(default_factory=dict)
    starting_gold: dict[str, int] = Field(default_factory=dict)
    philosophy: InventoryPhilosophy | None = None
