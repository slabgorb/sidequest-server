"""Module-level helpers extracted from session_handler.py.

Pure functions only — no references to ``WebSocketSessionHandler``.
``_SessionData`` and ``SessionRoom`` appear here only as type annotations
(stringified by ``from __future__ import annotations``) so this module
imports them under ``TYPE_CHECKING`` to avoid circular imports.

Re-exported by ``session_handler.py`` for back-compat with tests and
external callers that import these symbols from there.
"""

from __future__ import annotations

import json
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
from sidequest.game.shared_world_delta import (
    build_shared_world_delta,
    merge_shared_delta_into_snapshot,
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
from sidequest.telemetry.spans import (
    npc_reinvented_span,
    orchestrator_notorious_party_gate_span,
    room_state_injected_span,
)

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
        out.append(
            MessageEnvelope(
                kind="SECRET_NOTE",
                payload_json=json.dumps(payload),
                origin_seq=0,
            )
        )
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


def _resolve_acting_character_name(sd: _SessionData, room: SessionRoom | None) -> str:
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
            if pid == sd.player_id and any(c.core.name == slot for c in snapshot.characters):
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
    all_defs = sd.genre_pack.rules.confrontations if sd.genre_pack.rules else []
    available_confrontations: list[tuple[str, str, str]] = [
        (
            cd.confrontation_type,
            cd.label,
            getattr(cd, "category", "") or "",
        )
        for cd in all_defs
    ]
    if encounter is not None and not encounter.resolved:
        in_encounter = True
        confrontation_def = find_confrontation_def(all_defs, encounter.encounter_type)
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
    npc_cores_by_name: dict[str, CreatureCore] = {npc.core.name: npc.core for npc in snapshot.npcs}

    # Story 45-8 — Notorious-party gating on session.player_count.
    #
    # Playtest 3 regression (evropi/pumblestone): a solo session whose
    # snapshot still carried the canonical full-party cast (Rux, Hant,
    # Ludzo, ...) leaked those names into the narrator's prose because
    # the peer filter only excluded ``char_name`` — it did not check
    # session player_count. The gate below drops every snapshot peer
    # when the room reports a single playing player, and redacts the
    # ``snapshot.characters`` JSON in ``state_summary`` so the names
    # cannot ride in via the game-state block either.
    #
    # AC4 (No silent fallbacks): if ``room`` is None the gate machinery
    # is unreachable. We default to safe-empty (no peers) AND emit a
    # WARNING — never silently fall through to "all snapshot characters
    # are peers".
    if room is None:
        player_count_for_gate = 0
        gate_engaged = True
        logger.warning(
            "orchestrator.notorious_party_gate room=None — gate machinery "
            "unreachable, defaulting to safe-empty party_peers "
            "(notorious_party_gated=true, party_context_available=false). "
            "session.player_count unknown.",
        )
    else:
        # ``non_abandoned_player_count`` is the right source of truth: it
        # counts seats in CHARGEN/PLAYING (every "live" lobby slot), which
        # matches the bug surface — a solo save where only Pumblestone has
        # a seat — without requiring every peer to have transitioned to
        # PLAYING (the failing-precondition for ``playing_player_count``
        # mid-chargen). ABANDONED orphans correctly drop out.
        count_method = getattr(room, "non_abandoned_player_count", None) or getattr(
            room, "playing_player_count", None
        )
        try:
            player_count_for_gate = int(count_method())
        except Exception:  # noqa: BLE001 — fail loud on any contract drift
            logger.warning(
                "orchestrator.notorious_party_gate "
                "player_count lookup raised — defaulting to safe-empty "
                "(notorious_party_gated=true, party_context_available=false).",
                exc_info=True,
            )
            player_count_for_gate = 0
        # AC1/AC2: gate is strict `== 1` (solo). `> 1` passes. `<= 0` is
        # an impossible state in normal operation (no seated players AND
        # we're trying to build a turn) — treat as fail-loud-empty rather
        # than re-opening the leak.
        if player_count_for_gate == 1:
            gate_engaged = True
        elif player_count_for_gate > 1:
            gate_engaged = False
        else:
            logger.warning(
                "orchestrator.notorious_party_gate "
                "player_count=%d (<= 0) — impossible state, defaulting "
                "to safe-empty (notorious_party_gated=true).",
                player_count_for_gate,
            )
            gate_engaged = True

    if gate_engaged:
        party_peers: list[PartyPeer] = []
    else:
        # Story 37-36: peer-identity packets, acting PC excluded.
        party_peers = [
            PartyPeer.from_character(pc) for pc in snapshot.characters if pc.core.name != char_name
        ]
    party_context_available = bool(party_peers)

    # AC3 — Fire the gate-decision span on EVERY turn. The GM panel
    # filters on this so Sebastien can see whether the gate engaged.
    with orchestrator_notorious_party_gate_span(
        player_count=player_count_for_gate,
        notorious_party_gated=gate_engaged,
        party_context_available=party_context_available,
    ):
        logger.info(
            "orchestrator.notorious_party_gate "
            "session.player_count=%d notorious_party_gated=%s "
            "party_context_available=%s",
            player_count_for_gate,
            gate_engaged,
            party_context_available,
        )

    # Story 45-1 — sealed-letter shared-world handshake. Build the
    # canonical delta, merge it back, and attach to state_summary so the
    # narrator sees ground-truth party adjacency. Without this the
    # narrator fabricates separations ("collapsed corridor" — playtest 3).
    # The merge is idempotent on a fresh snapshot (all fields already
    # match) — its job is to fire the OTEL event and provide MergeResult.
    handshake_delta = build_shared_world_delta(snapshot, room=room)
    merge_shared_delta_into_snapshot(snapshot, handshake_delta)
    state_summary_payload = json.loads(snapshot.model_dump_json())
    # Story 45-8 — when the gate is engaged, also redact non-self PCs
    # from the state_summary JSON. Without this redaction the canonical
    # party names ride into the narrator's <game_state> block via the
    # snapshot dump even though ``ctx.party_peers`` is empty.
    if gate_engaged and isinstance(state_summary_payload.get("characters"), list):
        state_summary_payload["characters"] = [
            entry
            for entry in state_summary_payload["characters"]
            if isinstance(entry, dict)
            and (
                entry.get("core", {}).get("name") == char_name
                if isinstance(entry.get("core"), dict)
                else entry.get("name") == char_name
            )
        ]
    state_summary_payload["party_formation"] = [
        entry.model_dump() for entry in handshake_delta.party_formation
    ]
    state_summary_payload["shared_world_delta"] = handshake_delta.model_dump()

    # Story 45-13 — per-room container retrieved-state injection. Read
    # the current room's RoomState (if any) and surface a count via the
    # ``room.state_injected`` span. The span fires on EVERY narrator
    # turn — including the no-prior-retrievals case
    # (``retrieved_container_count=0``) — because Sebastien's
    # lie-detector must be able to distinguish "gate engaged with
    # nothing to report" from "gate not engaged at all". The room is
    # keyed off ``snapshot.location``, the canonical "where the player
    # is right now" string. The retrieved-container payload also flows
    # into ``state_summary_payload`` automatically because line 312
    # already serializes the full ``snapshot`` (which includes
    # ``room_states``) into the narrator's <game_state> block.
    current_room_id = snapshot.location
    if not current_room_id:
        # No silent fallback (CLAUDE.md): a turn without a canonical
        # location renders the room-state gate unreachable — the span
        # would fire with ``room_id=""`` and look indistinguishable from
        # a valid empty room. Log a warning so the GM panel can spot
        # the configuration gap. The span still fires below (so
        # Sebastien's lie-detector keeps its no-op case) but with
        # ``room_id=""`` AND a logged warning.
        logger.warning(
            "state.room_state_injected_unreachable reason=snapshot_location_empty interaction=%d",
            snapshot.turn_manager.interaction,
        )
        current_room_id = ""
    current_room_state = snapshot.room_states.get(current_room_id)
    retrieved_container_count = (
        sum(1 for c in current_room_state.containers.values() if c.retrieved)
        if current_room_state is not None
        else 0
    )
    with room_state_injected_span(
        room_id=current_room_id,
        retrieved_container_count=retrieved_container_count,
        interaction=snapshot.turn_manager.interaction,
    ):
        logger.info(
            "state.room_state_injected room=%s retrieved_count=%d",
            current_room_id,
            retrieved_container_count,
        )

    state_summary_json = json.dumps(state_summary_payload, indent=2)

    # Orbital tier fields — populated from Session when the room has one.
    # When room is None (test fixtures, legacy paths) the fields default
    # to None/empty, which causes build_narrator_prompt to skip the
    # <courses> block entirely — zero byte leak, no silent fallback.
    orbital_content = None
    orbital_scope = None
    party_body_id = None
    recent_body_mentions: list[str] = []
    quest_anchors: list[str] = []
    if room is not None:
        sess = room.session
        orbital_content = sess.orbital_content
        orbital_scope = sess.orbital_scope  # always returns Scope (never None)
        party_body_id = sess.party_body_id
        recent_body_mentions = list(sess.recent_body_mentions)
        quest_anchors = list(snapshot.quest_anchors)

    return TurnContext(
        in_combat=in_combat,
        in_chase=in_chase,
        in_encounter=in_encounter,
        encounter=encounter if in_encounter else None,
        confrontation_def=confrontation_def,
        available_confrontations=available_confrontations,
        encounter_summary=encounter_summary,
        state_summary=state_summary_json,
        narrator_verbosity="standard",
        narrator_vocabulary="literary",
        genre=sd.genre_slug,
        genre_prompts=sd.genre_pack.prompts,
        character_name=char_name,
        current_location=(
            _resolve_location_display(sd.genre_pack, sd.world_slug, snapshot.location) or "Unknown"
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
        orbital_content=orbital_content,
        orbital_scope=orbital_scope,
        party_body_id=party_body_id,
        recent_body_mentions=recent_body_mentions,
        quest_anchors=quest_anchors,
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
    SIDEQUEST_OUTPUT_DIR — UI 404 beats silent replacement. Each
    fallthrough emits an ``image_unavailable`` watcher event so the GM
    panel surfaces "image generated but not deliverable" rather than
    looking like nothing happened (CLAUDE.md OTEL principle).
    """
    import os as _os
    import pathlib as _pathlib

    root = _os.environ.get("SIDEQUEST_OUTPUT_DIR")
    if not root or not image_path:
        _publish_image_unavailable(image_path, reason="output_dir_unset")
        return image_path
    try:
        rel = _pathlib.Path(image_path).resolve().relative_to(_pathlib.Path(root).resolve())
    except ValueError:
        _publish_image_unavailable(image_path, reason="path_outside_output_dir")
        return image_path
    return "/renders/" + str(rel).replace(_os.sep, "/")


def _publish_image_unavailable(image_path: str, *, reason: str) -> None:
    """Emit a watcher event for an unrewriteable render path. Lazy import
    avoids a server↔telemetry import cycle at module load."""
    try:
        from sidequest.telemetry.watcher_hub import publish_event
    except ImportError:
        return
    publish_event(
        "image_unavailable",
        {"image_path": image_path, "reason": reason},
        component="render",
        severity="warning",
    )


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
