"""Module-level helpers extracted from session_handler.py.

Pure functions only — no references to ``WebSocketSessionHandler``.
``_SessionData`` and ``SessionRoom`` appear here only as type annotations
(stringified by ``from __future__ import annotations``) so this module
imports them under ``TYPE_CHECKING`` to avoid circular imports.

Re-exported by ``session_handler.py`` for back-compat with tests and
external callers that import these symbols from there.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.agents.orchestrator import NpcMention, TurnContext
from sidequest.game.builder import humanize_snake_case
from sidequest.game.creature_core import CreatureCore
from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.session import (
    NpcRegistryEntry,
    PartyPeer,
)
from sidequest.genre.models.pack import GenrePack
from sidequest.protocol.dispatch import DispatchPackage
from sidequest.protocol.messages import (
    ErrorMessage,
    ErrorPayload,
    PlayerPresenceMessage,
    PlayerPresencePayload,
)
from sidequest.protocol.types import NonBlankString
from sidequest.telemetry.spans import npc_reinvented_span

if TYPE_CHECKING:
    from sidequest.server.session_handler import _SessionData
    from sidequest.server.session_room import SessionRoom

logger = logging.getLogger(__name__)


def build_secret_note_events(
    removed: list,
    *,
    turn_id: str,
) -> list[MessageEnvelope]:
    """Build SECRET_NOTE envelopes from prompt-redacted dispatch entries.

    Group G Task 6. ``removed`` is the second element of the tuple returned
    by :func:`sidequest.agents.prompt_redaction.redact_dispatch_package`.
    Only ``SubsystemDispatch`` entries produce SECRET_NOTE events;
    ``NarratorDirective`` and ``LethalityVerdict`` fall through.
    ``origin_seq=0`` — the event-log append assigns the real seq.
    """
    import json

    from sidequest.protocol.dispatch import SubsystemDispatch

    out: list[MessageEnvelope] = []
    for entry in removed:
        if not isinstance(entry, SubsystemDispatch):
            continue
        payload = {
            "turn_id": turn_id,
            "idempotency_key": entry.idempotency_key,
            "subsystem": entry.subsystem,
            "params": entry.params,
            "_visibility": {
                "visible_to": entry.visibility.visible_to,
                "fidelity": entry.visibility.perception_fidelity,
            },
        }
        out.append(MessageEnvelope(
            kind="SECRET_NOTE",
            payload_json=json.dumps(payload),
            origin_seq=0,
        ))
    return out


def emit_secret_notes(
    *,
    secret_routes: list,
    turn_id: str,
    event_log,
) -> None:
    """Append SECRET_NOTE events for every redacted dispatch on the turn."""
    for envelope in build_secret_note_events(secret_routes, turn_id=turn_id):
        event_log.append(kind=envelope.kind, payload_json=envelope.payload_json)


def aggregate_visibility(pkg: DispatchPackage) -> dict:
    """Build the _visibility sidecar for the canonical narration payload.

    visible_to = union of non-redacted tags' visible_to lists; "all" is
    a stop-word that collapses the union. fidelity maps merge.
    """
    any_all = False
    union: set[str] = set()
    fidelity: dict[str, str] = {}
    for pd in pkg.per_player:
        for d in pd.dispatch:
            if d.visibility.redact_from_narrator_canonical:
                continue
            if d.visibility.visible_to == "all":
                any_all = True
            else:
                union.update(d.visibility.visible_to)
            fidelity.update(d.visibility.perception_fidelity)
    return {
        "visible_to": "all" if any_all else sorted(union),
        "fidelity": fidelity,
    }


def _resolve_acting_character_name(
    sd: _SessionData, room: SessionRoom | None
) -> str:
    """Identify the requesting socket's PC by player_id via the room seat
    map. Returning the wrong name causes the narrator's party-peer block
    to misidentify peers (the playtest "Shirley is Laverne's hireling" bug).

    Resolution: room seat → snapshot match by player_name → first PC →
    lobby player_name (empty-snapshot fallback).
    """
    snapshot = sd.snapshot
    if not snapshot.characters:
        return sd.player_name
    seat_lookup = getattr(room, "slot_to_player_id", None) if room is not None else None
    if callable(seat_lookup) and sd.player_id:
        seat_map = seat_lookup()
        for slot, pid in seat_map.items():
            if pid == sd.player_id and any(
                c.core.name == slot for c in snapshot.characters
            ):
                return slot
    for char in snapshot.characters:
        if char.core.name == sd.player_name:
            return char.core.name
    return snapshot.characters[0].core.name


def _build_turn_context(
    sd: _SessionData,
    *,
    opening_directive: str | None = None,
    lore_context: str | None = None,
    room: SessionRoom | None = None,
) -> TurnContext:
    """Assemble :class:`TurnContext` for one narration turn (Slice H).

    ``opening_directive`` is consumed turn 0 only (caller clears the
    session field). ``lore_context`` is the pre-rendered <lore> block.
    ``room`` provides the seat map so MP can identify the acting PC by
    player_id rather than guessing snapshot.characters[0].
    """
    from sidequest.agents.encounter_render import render_encounter_summary
    from sidequest.server.dispatch.confrontation import find_confrontation_def

    snapshot = sd.snapshot
    char_name = _resolve_acting_character_name(sd, room)

    # Encounter flags from snapshot.encounter (Story 3.4). Category-based
    # flags from the matched ConfrontationDef; skip resolved encounters
    # so a closed combat doesn't keep flipping in_combat=True.
    encounter = snapshot.encounter
    confrontation_def = None
    encounter_summary = None
    in_combat = False
    in_chase = False
    in_encounter = False
    if encounter is not None and not encounter.resolved:
        in_encounter = True
        defs = sd.genre_pack.rules.confrontations if sd.genre_pack.rules else []
        confrontation_def = find_confrontation_def(defs, encounter.encounter_type)
        if confrontation_def is not None:
            in_combat = confrontation_def.category == "combat"
            in_chase = confrontation_def.category == "movement"
        encounter_summary = render_encounter_summary(encounter)

    # Group C — LethalityArbiter inputs. PCs mapped to owning player_id
    # via the room seat table; the acting socket's PC also lands under
    # sd.player_id so the arbiter can find the actor directly.
    pc_cores_by_player: dict[str, CreatureCore] = {}
    seat_lookup_fn = getattr(room, "slot_to_player_id", None) if room is not None else None
    seat_map: dict[str, str] = seat_lookup_fn() if callable(seat_lookup_fn) else {}
    char_to_player = dict(seat_map.items())
    for pc in snapshot.characters:
        owner_pid = char_to_player.get(pc.core.name)
        if owner_pid is None and pc.core.name == char_name:
            owner_pid = sd.player_id
        if owner_pid:
            pc_cores_by_player[owner_pid] = pc.core
    npc_cores_by_name: dict[str, CreatureCore] = {
        npc.core.name: npc.core for npc in snapshot.npcs
    }

    # Story 37-36: peer-identity packets, acting PC excluded.
    party_peers: list[PartyPeer] = [
        PartyPeer.from_character(pc)
        for pc in snapshot.characters
        if pc.core.name != char_name
    ]

    return TurnContext(
        in_combat=in_combat,
        in_chase=in_chase,
        in_encounter=in_encounter,
        encounter=encounter if in_encounter else None,
        confrontation_def=confrontation_def,
        encounter_summary=encounter_summary,
        state_summary=snapshot.model_dump_json(indent=2),
        narrator_verbosity="standard",
        narrator_vocabulary="literary",
        genre=sd.genre_slug,
        genre_prompts=sd.genre_pack.prompts,
        character_name=char_name,
        current_location=(
            _resolve_location_display(
                sd.genre_pack, sd.world_slug, snapshot.location
            )
            or "Unknown"
        ),
        available_sfx=_sfx_ids_from_genre(sd.genre_pack),
        npc_registry=list(snapshot.npc_registry),
        npcs=list(snapshot.npcs),
        party_peers=party_peers,
        opening_directive=opening_directive,
        world_context=sd.world_context,
        lore_context=lore_context,
        lethality_policy=sd.genre_pack.lethality_policy,
        pc_cores_by_player=pc_cores_by_player,
        npc_cores_by_name=npc_cores_by_name,
    )


def _find_confrontation_def(pack: GenrePack, confrontation_type: str) -> object | None:
    """Match the narrator's confrontation hint to a pack ConfrontationDef.

    None → caller skips encounter context injection (narration-only fallback).
    """
    rules = getattr(pack, "rules", None)
    if rules is None:
        return None
    confrontations = getattr(rules, "confrontations", None) or []
    for conf_def in confrontations:
        if conf_def.confrontation_type == confrontation_type:
            return conf_def
    return None


def _world_history_value(pack: GenrePack, world_slug: str) -> object | None:
    """Raw world ``history.yaml`` payload, or None when absent.

    ``materialize_from_genre_pack`` treats None as zero chapters,
    yielding a snapshot with just genre/world slugs set.
    """
    world = pack.worlds.get(world_slug)
    if world is None:
        return None
    return world.history


def _error_msg(
    message: str,
    reconnect_required: bool = False,
    *,
    code: str | None = None,
) -> ErrorMessage:
    return ErrorMessage(
        type="ERROR",  # type: ignore[arg-type]
        payload=ErrorPayload(
            message=NonBlankString(message),
            reconnect_required=reconnect_required,
            code=code,
        ),
        player_id="",
    )


def _presence_msg(player_id: str, state: str) -> PlayerPresenceMessage:
    """PLAYER_PRESENCE message for connect/disconnect (MP-02 Task 4)."""
    return PlayerPresenceMessage(
        payload=PlayerPresencePayload(player_id=player_id, state=state),  # type: ignore[arg-type]
    )


def _resolve_location_display(
    pack: GenrePack | None,
    world_slug: str | None,
    location: str | None,
) -> str:
    """Render a location id as a UI display name.

    Cartography room name > snake_case humanization > raw value.
    Empty input → empty string.
    """
    if not location:
        return ""
    if pack is not None and world_slug:
        world = pack.worlds.get(world_slug)
        if world is not None:
            cart = getattr(world, "cartography", None)
            rooms = getattr(cart, "rooms", None) if cart is not None else None
            if rooms:
                for room in rooms:
                    if getattr(room, "id", None) == location:
                        return room.name
    if "_" in location and location == location.lower():
        return humanize_snake_case(location)
    return location


def _sfx_ids_from_genre(genre_pack: GenrePack) -> list[str]:
    """Extract SFX IDs from genre audio config."""
    if genre_pack.audio is None:
        return []
    sfx_lib = getattr(genre_pack.audio, "sfx_library", None)
    if not sfx_lib:
        return []
    if isinstance(sfx_lib, list):
        return [str(getattr(s, "id", s)) for s in sfx_lib]
    return []


def _render_url_from_path(image_path: str) -> str:
    """Translate a daemon filesystem path into a /renders/* URL.

    Returns the absolute path verbatim when it isn't inside
    SIDEQUEST_OUTPUT_DIR — UI 404 beats silent replacement.
    """
    import os as _os
    import pathlib as _pathlib

    root = _os.environ.get("SIDEQUEST_OUTPUT_DIR")
    if not root or not image_path:
        return image_path
    try:
        rel = _pathlib.Path(image_path).resolve().relative_to(
            _pathlib.Path(root).resolve()
        )
    except ValueError:
        return image_path
    return "/renders/" + str(rel).replace(_os.sep, "/")


def _detect_npc_identity_drift(
    existing: NpcRegistryEntry,
    mention: NpcMention,
    turn_num: int,
) -> None:
    """Warn when narrator NPC mention disagrees with the canonical entry.

    Story 37-44. Empty fields on the mention = "no opinion"; only explicit
    disagreement triggers. Side-effect only (logger.warning + watcher).
    """
    for field, m_val, e_val in (
        ("pronouns", mention.pronouns, existing.pronouns),
        ("role", mention.role, existing.role),
    ):
        if m_val and e_val and m_val.strip().lower() != e_val.strip().lower():
            # Span emission replaces the prior direct ``_watcher_publish`` —
            # ``WatcherSpanProcessor`` re-emits via
            # ``SPAN_ROUTES[SPAN_NPC_REINVENTED]`` and propagates the
            # ``severity="warning"`` attribute set by the helper.
            with npc_reinvented_span(
                npc_name=existing.name,
                drift_field=field,
                expected=e_val,
                narrator=m_val,
                turn_number=turn_num,
            ):
                logger.warning(
                    "npc.reinvented name=%r field=%s expected=%r narrator=%r turn=%d",
                    existing.name,
                    field,
                    e_val,
                    m_val,
                    turn_num,
                )
