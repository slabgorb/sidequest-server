"""Nested model types shared across Phase 1 protocol payloads.

Port of the sub-types defined in sidequest-protocol/src/message.rs that are
referenced transitively by the Phase 1 GameMessage payloads. All types live in
this single file — do not fragment into sub-modules.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sidequest.protocol.base import ProtocolBase
from sidequest.protocol.provenance import Provenance
from sidequest.protocol.types import NonBlankString

# ---------------------------------------------------------------------------
# FactCategory — from Footnote / Journal
# ---------------------------------------------------------------------------


class FactCategory(StrEnum):
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


class PartyFormationWireEntry(ProtocolBase):
    """Wire-side party formation entry — story 45-1 sealed-letter handshake.

    Mirrors :class:`sidequest.game.shared_world_delta.PartyFormationEntry`
    but lives on the protocol boundary so non-Python clients can decode it
    without pulling game-side types. Carries canonical placement only —
    perceived state (mood/tactics/personality) never lands here.
    """

    player_id: str
    """Player_id whose character occupies this slot."""
    location: str
    """Canonical room/POI for this PC."""
    adjacency: list[str]
    """Other player_ids sharing this location."""


class StateDelta(ProtocolBase):
    """State changes carried in NARRATION and TURN_STATUS.

    Port of sidequest_protocol::StateDelta.
    All fields are optional — only changed state is included.

    Story 45-1 added ``encounter_id`` and ``party_formation`` so the
    sealed-letter shared-world handshake can ride NARRATION_END alongside
    the existing location field.
    """

    location: str | None = None
    """New location, if changed."""
    characters: list[CharacterState] | None = None
    """Updated character states, merged by name."""
    quests: dict[str, str] | None = None
    """Updated quest statuses, merged by key."""
    items_gained: list[ItemGained] | None = None
    """Items gained by the player this turn."""
    encounter_id: str | None = None
    """Active encounter id (encounter_type), or None when no encounter is live."""
    party_formation: list[PartyFormationWireEntry] | None = None
    """Per-player canonical placement — story 45-1 sealed-letter handshake."""
    magic_state: dict | None = None
    """Opaque magic-state payload when MagicState changed this turn (Task 2.4).
    None when magic is inactive or unchanged. Client deserializes via TS types."""


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
# ClassRequirement — chargen the_arrangement live-qualify panel row
# ---------------------------------------------------------------------------


class ClassRequirement(ProtocolBase):
    """One row in the live-qualify panel during the_arrangement scene."""

    name: str
    """Class display name (e.g. 'Fighter')."""
    requirement_label: str
    """Human-readable requirement (e.g. 'STR 9+')."""


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
# CompanionMember — narrator-recruited NPC companion in PARTY_STATUS
# ---------------------------------------------------------------------------


class CompanionMember(ProtocolBase):
    """A narrator-recruited NPC companion (hireling / retainer / ally).

    Surfaced in PARTY_STATUS alongside PartyMember so the Party panel
    can render the full active roster (PCs + companions). Companions
    are NOT player-controlled — they have no Edge bar / inventory /
    sheet at this tier; this payload is the minimum state the panel
    needs to display them: name, role, panel description, and the
    contract notes the narrator authored on recruit.

    Playtest 2026-05-06 wiring fix.
    """

    name: NonBlankString
    """Display name. Non-blank — identity key for dismissal lookup."""
    role: str = ""
    """Hireling role in plain prose (torchbearer, porter, scout, etc.)."""
    description: str = ""
    """One-sentence narrator-authored description for panel tooltip."""
    notes: str = ""
    """Optional contract / terms one-liner."""
    recruited_turn: int = 0
    """Interaction turn at the moment of recruitment."""
    recruited_by: str = ""
    """Acting PC's name at recruit time — \"who is this companion bonded to.\""""


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
