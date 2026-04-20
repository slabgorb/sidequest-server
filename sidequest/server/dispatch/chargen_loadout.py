"""Starting-equipment loadout wiring for chargen confirmation.

Port of the loadout block inside ``dispatch_character_creation`` in
``sidequest-api/crates/sidequest-server/src/dispatch/connect.rs``
(lines ~1745-1864): after ``builder.build()`` produces a Character with
only builder-side item_hints, this module wires the class-specific
starting equipment from ``pack.inventory.starting_equipment`` into the
character's inventory, resolving item ids through ``item_catalog`` and
accumulating ``starting_gold``.

The Python ``Inventory.items`` field is a ``list[dict]`` (Phase 1
simplification — Rust uses the typed ``Item`` struct). Item dicts
produced here mirror the Rust ``Item`` JSON shape so save-file parity
holds across the port.
"""

from __future__ import annotations

import logging

from sidequest.game.character import Character
from sidequest.genre.models.inventory import CatalogItem, InventoryConfig

logger = logging.getLogger(__name__)


def _match_class(mapping_keys: list[str], class_name: str) -> str | None:
    """Case-insensitive lookup for ``starting_equipment[class]`` / ``starting_gold[class]``.

    Rust uses ``.iter().find(|(k, _)| k.to_lowercase() == class_lower)``;
    this preserves the first-match-wins semantics.
    """
    class_lower = class_name.casefold()
    for key in mapping_keys:
        if key.casefold() == class_lower:
            return key
    return None


def _item_dict_from_catalog(catalog_item: CatalogItem) -> dict:
    """Build the loadout item dict from a catalog entry.

    Mirrors the Rust ``Item`` JSON shape (connect.rs:1795-1812).
    """
    rarity = catalog_item.rarity.strip() or "common"
    return {
        "id": catalog_item.id,
        "name": catalog_item.name,
        "description": catalog_item.description,
        "category": catalog_item.category,
        "value": int(catalog_item.value),
        "weight": float(catalog_item.weight),
        "rarity": rarity,
        "narrative_weight": 0.3,
        "tags": list(catalog_item.tags),
        "equipped": False,
        "quantity": 1,
        "uses_remaining": catalog_item.resource_ticks,
        "state": "Carried",
    }


def _item_dict_minimal(item_id: str) -> dict:
    """Build a minimal item dict for ids that aren't in the catalog.

    Mirrors the Rust fallback branch (connect.rs:1814-1849). Used when a
    pack references an item id in ``starting_equipment`` that isn't
    declared in ``item_catalog`` — we still honor the loadout rather than
    silently dropping the item.
    """
    display = item_id.replace("_", " ")
    return {
        "id": item_id,
        "name": display,
        "description": "Starting equipment",
        "category": "equipment",
        "value": 0,
        "weight": 1.0,
        "rarity": "common",
        "narrative_weight": 0.2,
        "tags": [],
        "equipped": False,
        "quantity": 1,
        "uses_remaining": None,
        "state": "Carried",
    }


def apply_starting_loadout(
    character: Character, inventory_config: InventoryConfig | None
) -> tuple[int, int]:
    """Append class-specific starting equipment and gold to the character's inventory.

    Port of connect.rs:1745-1864. Mutates ``character.core.inventory`` in
    place — items append to ``inventory.items`` (builder-side hints are
    preserved), gold increments by ``starting_gold[class]``.

    Args:
        character: The built character (class already set).
        inventory_config: Genre pack inventory config; ``None`` means the
            pack has no inventory.yaml and we no-op.

    Returns:
        ``(items_added, gold_added)`` — for logging and assertion in
        tests. Both zero when the pack has no inventory config or when
        the character's class isn't in ``starting_equipment``.
    """
    if inventory_config is None:
        return (0, 0)

    class_name = character.char_class
    equipment_key = _match_class(
        list(inventory_config.starting_equipment.keys()), class_name
    )
    gold_key = _match_class(list(inventory_config.starting_gold.keys()), class_name)

    equipment_ids: list[str] = (
        inventory_config.starting_equipment[equipment_key] if equipment_key else []
    )
    gold: int = inventory_config.starting_gold[gold_key] if gold_key else 0

    catalog_by_id = {item.id: item for item in inventory_config.item_catalog}
    items_added = 0
    for item_id in equipment_ids:
        catalog_item = catalog_by_id.get(item_id)
        if catalog_item is not None:
            character.core.inventory.items.append(_item_dict_from_catalog(catalog_item))
        else:
            character.core.inventory.items.append(_item_dict_minimal(item_id))
        items_added += 1

    if gold:
        character.core.inventory.gold += gold

    if items_added or gold:
        logger.info(
            "chargen.starting_equipment — wired from inventory.yaml "
            "class=%s items_added=%d gold_added=%d",
            class_name,
            items_added,
            gold,
        )

    return (items_added, gold)
