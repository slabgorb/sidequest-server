"""Nested model types shared across Phase 1 protocol payloads.

Port of the sub-types defined in sidequest-protocol/src/message.rs that are
referenced transitively by the Phase 1 GameMessage payloads. All types live in
this single file — do not fragment into sub-modules.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from sidequest.protocol.base import ProtocolBase
from sidequest.protocol.provenance import Provenance
from sidequest.protocol.types import NonBlankString

# ---------------------------------------------------------------------------
# FactCategory — from Footnote / Journal
# ---------------------------------------------------------------------------


class FactCategory(str, Enum):
    """Classification category for narrator footnotes.

    Port of sidequest_protocol::FactCategory.
    """

    Lore = "Lore"
    """World history, mythology, or cosmology."""
    Place = "Place"
    """Geographic location or landmark."""
    Person = "Person"
    """NPC, faction, or named individual."""
    Quest = "Quest"
    """Quest objective, task, or mission."""
    Ability = "Ability"
    """Character ability, skill, or power."""


# ---------------------------------------------------------------------------
# Footnote — narration knowledge extraction
# ---------------------------------------------------------------------------


class Footnote(ProtocolBase):
    """A structured footnote from narrator output.

    Port of sidequest_protocol::Footnote.
    """

    marker: int | None = None
    """Marker number matching [N] superscript in prose. Optional."""
    fact_id: str | None = None
    """Links to existing KnownFact if this is a callback (is_new: false)."""
    summary: NonBlankString
    """One-sentence description of the fact. Non-blank."""
    category: FactCategory
    """Classification category for the footnote."""
    is_new: bool
    """True if this is a new revelation, false if referencing prior knowledge."""


# ---------------------------------------------------------------------------
# ItemGained — inventory addition during narration
# ---------------------------------------------------------------------------


class ItemGained(ProtocolBase):
    """An item the player gained during narration.

    Port of sidequest_protocol::ItemGained.
    """

    name: NonBlankString
    """Short item name. Non-blank."""
    description: NonBlankString = Field(
        default_factory=lambda: NonBlankString.model_validate("An item found during adventure.")
    )
    """One-sentence description. Non-blank."""
    category: str = "misc"
    """Category (weapon, armor, tool, consumable, quest, misc)."""


# ---------------------------------------------------------------------------
# CharacterState — character snapshot in state deltas
# ---------------------------------------------------------------------------


class CharacterState(ProtocolBase):
    """Character state as seen by the client (UI-facing).

    Port of sidequest_protocol::CharacterState.
    """

    name: NonBlankString
    """Character name (merge key). Non-blank."""
    hp: int
    """Current hit points."""
    max_hp: int
    """Maximum hit points."""
    level: int = 0
    """Character level."""
    class_: str = Field("", alias="class")
    """Character class (e.g., 'Ranger', 'Mage')."""
    statuses: list[str]
    """Active status effects."""
    inventory: list[str]
    """Inventory item names."""
    archetype_provenance: Provenance | None = None
    """Provenance of the resolved archetype, when available."""


# ---------------------------------------------------------------------------
# StateDelta — state mutations carried in NARRATION and TURN_STATUS
# ---------------------------------------------------------------------------


class StateDelta(ProtocolBase):
    """State changes carried in NARRATION and TURN_STATUS.

    Port of sidequest_protocol::StateDelta.
    All fields are optional — only changed state is included.
    """

    location: str | None = None
    """New location, if changed."""
    characters: list[CharacterState] | None = None
    """Updated character states, merged by name."""
    quests: dict[str, str] | None = None
    """Updated quest statuses, merged by key."""
    items_gained: list[ItemGained] | None = None
    """Items gained by the player this turn."""


# ---------------------------------------------------------------------------
# InitialState — session boot state
# ---------------------------------------------------------------------------


class InitialState(ProtocolBase):
    """Initial game state sent on session ready.

    Port of sidequest_protocol::InitialState.
    """

    characters: list[CharacterState]
    """Party characters."""
    location: NonBlankString
    """Current location. Non-blank."""
    quests: dict[str, str]
    """Quest log."""
    turn_count: int = 0
    """Current turn count (persisted across sessions)."""


# ---------------------------------------------------------------------------
# CreationChoice — chargen option
# ---------------------------------------------------------------------------


class CreationChoice(ProtocolBase):
    """A choice in the character creation flow.

    Port of sidequest_protocol::CreationChoice.
    """

    label: NonBlankString
    """Display label. Non-blank — rendered as the button text."""
    description: NonBlankString
    """Description text. Non-blank — rendered below the label."""


# ---------------------------------------------------------------------------
# RolledStat — chargen rolled ability score
# ---------------------------------------------------------------------------


class RolledStat(ProtocolBase):
    """One rolled ability score: ability name + value.

    Port of sidequest_protocol::RolledStat.
    """

    name: str
    """Ability name as defined by the genre's ability_score_names."""
    value: int
    """Rolled value (typically 3-18 for 3d6 strict)."""


# ---------------------------------------------------------------------------
# InventoryItem — item entry in inventory
# ---------------------------------------------------------------------------


class InventoryItem(ProtocolBase):
    """An inventory item.

    Port of sidequest_protocol::InventoryItem.
    """

    name: NonBlankString
    """Item name. Non-blank — rendered as the inventory row header."""
    item_type: str = Field(alias="type")
    """Item category (weapon, armor, consumable, etc.)."""
    equipped: bool
    """Whether the item is equipped."""
    quantity: int
    """Stack count."""
    description: NonBlankString
    """Item description. Non-blank — rendered below the name."""


# ---------------------------------------------------------------------------
# InventoryPayload — full inventory snapshot
# ---------------------------------------------------------------------------


class InventoryPayload(ProtocolBase):
    """Full inventory snapshot.

    Port of sidequest_protocol::InventoryPayload.
    """

    items: list[InventoryItem]
    """All inventory items."""
    gold: int
    """Currency amount. Numeric — the noun is ``currency_name`` below."""
    currency_name: str | None = None
    """Genre-declared currency noun (e.g. "credits" for space_opera,
    "Salvage" for mutant_wasteland, "gold" for caverns_and_claudes). Read
    from ``inventory.yaml::currency.name`` on the active genre pack.
    ``None`` when the genre pack doesn't declare one — UI falls back to
    a neutral default rather than hardcoding "gold" (which leaks fantasy
    tone into space/cyberpunk/etc. packs)."""


# ---------------------------------------------------------------------------
# CharacterSheetDetails — full character sheet nested inside PartyMember
# ---------------------------------------------------------------------------


class CharacterSheetDetails(ProtocolBase):
    """Character sheet details nested inside PartyMember.

    Port of sidequest_protocol::CharacterSheetDetails.
    """

    race: NonBlankString
    """Character race/origin. Non-blank post-chargen."""
    stats: dict[str, int]
    """Ability scores / stats."""
    abilities: list[str]
    """Known abilities."""
    backstory: NonBlankString
    """Character backstory. Non-blank post-chargen."""
    personality: NonBlankString
    """Personality trait. Non-blank post-chargen."""
    pronouns: NonBlankString | None = None
    """Pronouns. Optional. Non-blank when present."""
    equipment: list[str] = Field(default_factory=list)
    """Equipped/carried items as display strings."""


# ---------------------------------------------------------------------------
# PartyMember — character party snapshot
# ---------------------------------------------------------------------------


class PartyMember(ProtocolBase):
    """A party member in PARTY_STATUS.

    Port of sidequest_protocol::PartyMember.
    """

    player_id: NonBlankString
    """Player identifier. Non-blank — identity key."""
    name: NonBlankString
    """Player lobby name. Non-blank."""
    character_name: NonBlankString | None = None
    """In-game character name. Optional (None = still in chargen)."""
    current_hp: int
    """Current HP."""
    max_hp: int
    """Maximum HP."""
    statuses: list[str]
    """Active statuses."""
    class_: NonBlankString = Field(alias="class")
    """Character class. Non-blank."""
    level: int
    """Character level."""
    portrait_url: str | None = None
    """Portrait URL."""
    current_location: NonBlankString | None = None
    """Current location name. Optional."""
    sheet: CharacterSheetDetails | None = None
    """Full character sheet. None until chargen completes."""
    inventory: InventoryPayload | None = None
    """Full inventory snapshot. None until the member has a loadout."""


# ---------------------------------------------------------------------------
# TacticalGridPayload — grid layout
# ---------------------------------------------------------------------------


class TacticalFeaturePayload(ProtocolBase):
    """A named feature placed on the grid via legend glyph.

    Port of sidequest_protocol::TacticalFeaturePayload.
    """

    glyph: str
    """The uppercase letter glyph (A-Z) from the ASCII grid."""
    feature_type: str
    """Feature type (cover, hazard, difficult_terrain, atmosphere, interactable, door)."""
    label: NonBlankString
    """Human-readable label for UI tooltip. Non-blank."""
    positions: list[list[int]]
    """Grid positions where this feature appears ([x, y] pairs)."""


class TacticalGridPayload(ProtocolBase):
    """Grid layout — cell types as strings for JSON simplicity.

    Port of sidequest_protocol::TacticalGridPayload.
    """

    width: int
    """Grid width in cells."""
    height: int
    """Grid height in cells."""
    cells: list[list[str]]
    """2D grid of cell type strings (e.g., 'floor', 'wall', 'water')."""
    features: list[TacticalFeaturePayload]
    """Named features placed on the grid via legend."""


# ---------------------------------------------------------------------------
# RoomExitInfo — exit descriptor for room graph mode
# ---------------------------------------------------------------------------


class RoomExitInfo(ProtocolBase):
    """Exit descriptor for room graph mode — target room and exit type.

    Port of sidequest_protocol::RoomExitInfo.
    """

    target: NonBlankString
    """Target room ID this exit leads to. Non-blank."""
    exit_type: str
    """Exit type: 'door', 'corridor', 'chute_down', 'chute_up', 'secret'."""


# ---------------------------------------------------------------------------
# ExploredLocation — map location
# ---------------------------------------------------------------------------


class ExploredLocation(ProtocolBase):
    """A location on the explored map.

    Port of sidequest_protocol::ExploredLocation.
    """

    id: str = ""
    """Stable location identifier. Empty string for legacy saves."""
    name: NonBlankString
    """Display name (human-readable). Non-blank."""
    x: int = 0
    """X coordinate on map."""
    y: int = 0
    """Y coordinate on map."""
    location_type: str = Field("", alias="type")
    """Location type (dungeon, town, etc.)."""
    connections: list[str] = Field(default_factory=list)
    """Connected location names."""
    room_exits: list[RoomExitInfo] = Field(default_factory=list)
    """Room exits with target and type info (room graph mode only)."""
    room_type: str = ""
    """Room type from RoomDef (room graph mode only)."""
    size: tuple[int, int] | None = None
    """Room dimensions (width, height) from RoomDef."""
    is_current_room: bool = False
    """Whether this is the player's current room."""
    tactical_grid: TacticalGridPayload | None = None
    """Tactical grid data for rooms with ASCII grids."""


# ---------------------------------------------------------------------------
# FogBounds — map visibility
# ---------------------------------------------------------------------------


class FogBounds(ProtocolBase):
    """Fog of war bounds for map overlay.

    Port of sidequest_protocol::FogBounds.
    """

    width: int
    """Map width."""
    height: int
    """Map height."""


# ---------------------------------------------------------------------------
# CartographyRegion — region data
# ---------------------------------------------------------------------------


class CartographyRegion(ProtocolBase):
    """A region in the cartography metadata (wire format for UI).

    Port of sidequest_protocol::CartographyRegion.
    """

    name: NonBlankString
    """Display name. Non-blank — regions without names cannot be rendered."""
    description: str = ""
    """Description."""
    adjacent: list[str] = Field(default_factory=list)
    """Adjacent region slugs."""


# ---------------------------------------------------------------------------
# CartographyRoute — route descriptor
# ---------------------------------------------------------------------------


class CartographyRoute(ProtocolBase):
    """A route between regions in the cartography metadata (wire format for UI).

    Port of sidequest_protocol::CartographyRoute.
    """

    name: NonBlankString
    """Route name. Non-blank — the UI uses this as the route label."""
    description: str = ""
    """Description."""
    from_id: str | None = None
    """Source region slug."""
    to_id: str | None = None
    """Destination region slug."""


# ---------------------------------------------------------------------------
# CartographyMetadata — world map structure
# ---------------------------------------------------------------------------


class CartographyMetadata(ProtocolBase):
    """Cartography metadata for the map overlay.

    Port of sidequest_protocol::CartographyMetadata.
    """

    navigation_mode: str
    """Navigation mode — 'region', 'room_graph', or 'hierarchical'."""
    starting_region: str = ""
    """Starting region slug."""
    regions: dict[str, CartographyRegion] = Field(default_factory=dict)
    """Regions keyed by slug."""
    routes: list[CartographyRoute] = Field(default_factory=list)
    """Routes between regions."""
