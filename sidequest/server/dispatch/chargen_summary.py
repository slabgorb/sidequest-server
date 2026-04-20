"""Confirmation-phase summary rendering for the character builder.

Port of ``sidequest-server/src/dispatch/chargen_summary.rs``.

Until 2026-04-09 this lived inside ``CharacterBuilder.to_scene_message`` in
the ``sidequest.game`` package (and the equivalent in the Rust source). That
was the wrong home: the builder is a state machine, and a faithful
confirmation summary needs inputs the builder does not own — specifically
the **lobby-provided player name** (there is no chargen scene for it in
genres like ``caverns_and_claudes``) and the **genre pack's
``starting_equipment`` table** (resolved from ``inventory.yaml``, not from
scene effects).

Keeping summary rendering inside the builder silently dropped those two
fields during the Thessa playtest bug on 2026-04-09. Moving it here
co-locates rendering with the data it requires: the server-side dispatch
layer already holds the ``GenrePack`` and the lobby name at every chargen
call site.

The builder stays responsible for the state machine; this module is the
view. New summary fields go here, not in the builder.
"""

from __future__ import annotations

from enum import Enum

from opentelemetry import trace

from sidequest.game.builder import CharacterBuilder, humanize_snake_case
from sidequest.genre.models.pack import GenrePack
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
)


class _NameSource(str, Enum):
    """Which source produced the Name line in the rendered summary."""

    NAME_SCENE = "name_scene"
    """A freeform name-entry scene in the builder (e.g. mutant_wasteland)."""
    LOBBY = "lobby"
    """The lobby-provided name passed via the ``connect`` payload."""
    NONE = "none"
    """No name available from either source."""


class _EquipmentSource(str, Enum):
    """Which source produced the Equipment line in the rendered summary."""

    SCENE_ITEM_HINTS = "scene_item_hints"
    """Accumulated ``item_hint`` mechanical effects from scene choices."""
    PACK_STARTING_EQUIPMENT = "pack_starting_equipment"
    """Looked up from ``pack.inventory.starting_equipment[class]``."""
    MERGED = "merged"
    """Both sources contributed (scene hints merged onto the class loadout)."""
    NONE = "none"
    """Neither source produced any equipment."""


def render_confirmation_summary(
    builder: CharacterBuilder,
    pack: GenrePack,
    lobby_name: str | None,
    player_id: str,
) -> CharacterCreationMessage:
    """Render the Confirmation-phase summary message for a builder.

    Port of ``render_confirmation_summary`` in ``chargen_summary.rs``.

    Pulls fields from three sources:

    1. **Builder state** — pronouns, stats, race/class hints, mutation/rig
       traits, backstory, and the name-entry-scene name (if the genre has
       one).
    2. **Lobby name** — fallback for the Name line when no name-entry scene
       exists. The precedence (scene > lobby) matches the precedence used at
       ``build()`` time in the dispatch handler.
    3. **Genre pack inventory** — ``starting_equipment[class]`` resolved via
       either the accumulated ``class_hint`` or, if absent, the genre's
       ``default_class`` from ``rules.yaml``. Item IDs are mapped to display
       names through ``pack.inventory.item_catalog`` when possible.

    Emits an OTEL event ``character_creation.confirmation_rendered``
    recording which sources fired, so the GM panel can catch silent
    regressions (empty Name line, missing Equipment line, etc.).
    """
    assert builder.is_confirmation(), (
        "render_confirmation_summary called outside Confirmation phase"
    )

    acc = builder.accumulated()
    parts: list[str] = []

    # --- Name (scene > lobby > omit) --------------------------------------
    scene_name = builder.character_name()
    if scene_name is not None:
        name_source = _NameSource.NAME_SCENE
        resolved_name: str | None = scene_name
    else:
        trimmed_lobby = lobby_name.strip() if lobby_name else ""
        if trimmed_lobby:
            name_source = _NameSource.LOBBY
            resolved_name = trimmed_lobby
        else:
            name_source = _NameSource.NONE
            resolved_name = None
    if resolved_name is not None:
        parts.append(f"Name: {resolved_name}")

    # --- Race / Class / Personality ---------------------------------------
    # Only show fields the chargen actually accumulated. Genres like
    # caverns_and_claudes deliberately omit race/class scenes — we don't lie
    # with "Unknown" for fields the genre doesn't define.
    if acc.race_hint is not None:
        parts.append(f"{builder.race_label()}: {acc.race_hint}")

    if acc.class_hint is not None:
        parts.append(f"{builder.class_label()}: {acc.class_hint}")
    elif (default_class := builder.default_class()) is not None:
        # If the genre has a default_class in rules.yaml (e.g. caverns
        # default_class: Delver), show it on the summary so the player sees
        # what class their equipment will be loaded for.
        parts.append(f"{builder.class_label()}: {default_class}")

    if acc.personality_trait is not None:
        parts.append(f"Personality: {acc.personality_trait}")

    if acc.pronoun_hint is not None:
        parts.append(f"Pronouns: {acc.pronoun_hint}")

    # --- Stats ------------------------------------------------------------
    rolled = builder.rolled_stats()
    if rolled is not None:
        stat_line = "  ".join(f"{name} {val}" for name, val in rolled)
        parts.append(f"Stats: {stat_line}")

    if acc.mutation_hint is not None:
        parts.append(f"Mutation: {humanize_snake_case(acc.mutation_hint)}")
    if acc.affinity_hint is not None:
        parts.append(f"Affinity: {acc.affinity_hint}")
    if acc.rig_type_hint is not None:
        parts.append(f"Rig: {acc.rig_type_hint}")
    if acc.rig_trait is not None:
        parts.append(f"Rig Trait: {acc.rig_trait}")

    # --- Equipment (merge scene hints with pack starting equipment) -------
    # Resolve the class used for the starting_equipment lookup the same way
    # the dispatch handler's confirmation branch does at build time:
    # prefer an explicit class_hint, otherwise fall back to the genre's
    # default_class from rules.yaml. This keeps the *preview* and the
    # *actual wired character* in sync by construction — no drift.
    lookup_class: str | None = acc.class_hint or builder.default_class()

    equipment_ids: list[str] = []
    used_scene_hints = False
    used_pack_starting = False

    if pack.inventory is not None and lookup_class is not None:
        class_lower = lookup_class.lower()
        for key, loadout in pack.inventory.starting_equipment.items():
            if key.lower() == class_lower:
                equipment_ids.extend(loadout)
                used_pack_starting = bool(loadout)
                break

    if acc.item_hints:
        for hint in acc.item_hints:
            if hint not in equipment_ids:
                equipment_ids.append(hint)
        used_scene_hints = True

    if used_pack_starting and used_scene_hints:
        equipment_source = _EquipmentSource.MERGED
    elif used_pack_starting:
        equipment_source = _EquipmentSource.PACK_STARTING_EQUIPMENT
    elif used_scene_hints:
        equipment_source = _EquipmentSource.SCENE_ITEM_HINTS
    else:
        equipment_source = _EquipmentSource.NONE

    if equipment_ids:
        display_items = [_resolve_item_display_name(pack, item_id) for item_id in equipment_ids]
        parts.append(f"Equipment: {', '.join(display_items)}")

    if acc.background is not None:
        parts.append(f"\nBackstory: {acc.background}")

    summary = "\n".join(parts)

    # --- Lie-detector telemetry -------------------------------------------
    # Records which sources fired so the GM panel can catch silent drops
    # (e.g. the 2026-04-09 Thessa bug: name_source=none, equipment_source=none
    # despite a lobby name being present and the pack defining a Delver
    # loadout).
    trace.get_current_span().add_event(
        "character_creation.confirmation_rendered",
        {
            "event": "confirmation_rendered",
            "name_source": name_source.value,
            "has_name": resolved_name is not None,
            "equipment_source": equipment_source.value,
            "equipment_count": len(equipment_ids),
            "lookup_class": lookup_class or "",
            "has_rolled_stats": rolled is not None,
            "player_id": player_id,
        },
    )

    payload = CharacterCreationPayload(
        phase="confirmation",
        scene_index=None,
        total_scenes=builder.total_scenes(),
        summary=summary,
    )
    return CharacterCreationMessage(payload=payload, player_id=player_id)


def _resolve_item_display_name(pack: GenrePack, item_id: str) -> str:
    """Map a starting-equipment item ID to a display name via
    ``pack.inventory.item_catalog``, falling back to Title-Cased snake_case
    if the catalog has no entry.
    """
    if pack.inventory is not None:
        for entry in pack.inventory.item_catalog:
            if entry.id == item_id and entry.name:
                return entry.name
    return humanize_snake_case(item_id)


__all__ = ["render_confirmation_summary"]
