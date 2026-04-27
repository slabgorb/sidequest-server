"""Event emission helpers extracted from WebSocketSessionHandler.

Phase 1 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes `handler: WebSocketSessionHandler` as its first
argument and operates on the handler's mutable state. No new abstractions
introduced — this is pure extraction with byte-identical behavior to the
original methods on WebSocketSessionHandler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sidequest.agents.perception_rewriter import rewrite_for_recipient

if TYPE_CHECKING:
    from sidequest.protocol.messages import ScrapbookEntryPayload
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData


def persist_scrapbook_entry(
    handler: WebSocketSessionHandler,
    payload: ScrapbookEntryPayload,
) -> None:
    """Insert a scrapbook row into the dedicated table (schema in
    ``game/persistence.py``). The table allows multiple rows per turn —
    no UNIQUE on turn_id.
    """
    import json as _json

    if handler._event_log is None:
        return  # Legacy non-slug path — no DB to write to
    store = handler._event_log.store
    npcs_json = _json.dumps(
        [
            {"name": ref.name, "role": ref.role, "disposition": ref.disposition}
            for ref in payload.npcs_present
        ]
    )
    facts_json = _json.dumps(list(payload.world_facts))
    with store._conn:
        store._conn.execute(
            "INSERT INTO scrapbook_entries "
            "(turn_id, scene_title, scene_type, location, image_url, "
            " narrative_excerpt, world_facts, npcs_present) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload.turn_id,
                payload.scene_title,
                payload.scene_type,
                payload.location,
                payload.image_url,
                payload.narrative_excerpt,
                facts_json,
                npcs_json,
            ),
        )


def emit_event(
    handler: WebSocketSessionHandler,
    kind: str,
    payload_model: object,
) -> object:
    """Persist an event to the EventLog and fan-out to all connected players.

    Invariants (per Plan 03):
    1. EventLog.append fires BEFORE any socket send.
    2. Fan-out consults ProjectionFilter per recipient.
    3. The emitter (handler) receives the raw, unfiltered event.

    Returns the outbound message object for the calling player (the emitter).
    Falls back to a plain message without seq when EventLog is unavailable
    (legacy non-slug connect path doesn't initialize _event_log).
    """
    import json

    from pydantic import BaseModel

    from sidequest.game.projection.envelope import MessageEnvelope
    from sidequest.game.projection_filter import FilterDecision
    from sidequest.server.session_handler import (
        _KIND_TO_MESSAGE_CLS,
        _project_frames,
        logger,
    )

    message_cls = _KIND_TO_MESSAGE_CLS.get(kind)
    if message_cls is None:
        raise ValueError(f"emit_event: unknown kind {kind!r}")

    event_log = handler._event_log
    projection_filter = handler._projection_filter

    # Serialize payload excluding seq (seq is assigned from the DB row)
    if isinstance(payload_model, BaseModel):
        payload_json = payload_model.model_dump_json(exclude={"seq"})
    else:
        payload_json = json.dumps(payload_model)  # type: ignore[arg-type]

    if event_log is not None:
        room = handler._room
        emitter_player_id = handler._session_data.player_id if handler._session_data else None

        # C2: event append + all cache writes share a single transaction.
        # Projections are computed inside the block so the cache row's
        # event_seq is the freshly-assigned one. If the server crashes
        # mid-block, sqlite rolls back both the event row and any partial
        # cache rows — either the event is fully persisted with its
        # projection cache, or not at all.
        store = event_log.store
        conn = store._conn
        fanout: list[tuple[str, FilterDecision, dict]] = []
        with conn:
            row = event_log.append_in_transaction(kind=kind, payload_json=payload_json, conn=conn)
            seq = row.seq

            if room is not None and projection_filter is not None:
                view = handler._build_game_state_view()
                envelope = MessageEnvelope(
                    kind=row.kind,
                    payload_json=row.payload_json,
                    origin_seq=row.seq,
                )
                # G6: status-effect perception overlay. Built once per
                # event (not per recipient) — snapshot statuses don't
                # change mid-fanout.
                status_effects = handler.status_effects_by_player()

                # G8: route through the shared write-split helper so the
                # per-peer filter loop is a single code path (test and
                # production exercise `_project_frames`).
                recipients = [
                    pid for pid in room.connected_player_ids() if pid != emitter_player_id
                ]

                def _cache_decision(pid: str, decision: FilterDecision) -> None:
                    if handler._projection_cache is not None:
                        handler._projection_cache.write_in_transaction(
                            event_seq=seq,
                            player_id=pid,
                            decision=decision,
                            conn=conn,
                        )

                decisions = _project_frames(
                    envelope=envelope,
                    projection_filter=projection_filter,
                    connected_players=recipients,
                    view=view,
                    on_decision=_cache_decision,
                )
                for other_pid, decision in decisions:
                    filtered_data: dict = {}
                    if decision.include:
                        filtered_data = json.loads(decision.payload_json)
                        # G6: PerceptionRewriter — strip spans whose kind
                        # is incompatible with the recipient's effective
                        # fidelity (base fidelity + status effects like
                        # blinded/deafened). Runs on the already-filtered
                        # payload, before WS send. Deterministic only;
                        # LLM re-voicing is deferred to post-MP.
                        filtered_data = rewrite_for_recipient(
                            canonical_payload=filtered_data,
                            viewer_player_id=other_pid,
                            status_effects=status_effects,
                        )
                    fanout.append((other_pid, decision, filtered_data))

        # Build emitter's message with raw, unfiltered payload + seq
        # (Invariant 3). model_copy with scalar update is safe here —
        # only `seq` is being added, no existing field is being replaced
        # with a filtered value.
        if isinstance(payload_model, BaseModel):
            emitter_payload = payload_model.model_copy(update={"seq": seq})
        else:
            emitter_payload = payload_model  # type: ignore[assignment]
        out_to_self = message_cls(payload=emitter_payload)

        # Socket fan-out happens AFTER the DB transaction commits. A
        # crash between commit and send is recoverable via the cache on
        # reconnect; sending before commit would risk a client observing
        # an event that never hit disk.
        if room is not None:
            payload_cls = type(payload_model) if isinstance(payload_model, BaseModel) else None
            for other_pid, decision, filtered_data in fanout:
                if not decision.include:
                    continue
                socket_id = room.socket_for_player(other_pid)
                if socket_id is None:
                    continue
                queue = room.queue_for_socket(socket_id)
                if queue is None:
                    continue
                try:
                    if payload_cls is not None:
                        # C3: rebuild the recipient payload from the
                        # filtered dict alone (plus seq). Do NOT use
                        # model_copy(update=...) — merging leaves fields
                        # absent from the filtered dict at their canonical
                        # values, which would leak any field a future rule
                        # drops entirely.
                        recipient_payload = payload_cls.model_validate(
                            {**filtered_data, "seq": seq}
                        )
                        recipient_msg = message_cls(payload=recipient_payload)
                    else:
                        recipient_msg = message_cls(payload={**filtered_data, "seq": seq})
                except Exception:
                    # Never silently fail fan-out; log and skip this recipient.
                    logger.error(
                        "emit_event.fanout_failed kind=%s other_pid=%s",
                        kind,
                        other_pid,
                    )
                    continue
                queue.put_nowait(recipient_msg)
    else:
        # Legacy path (non-slug connect): no EventLog, no seq
        out_to_self = message_cls(payload=payload_model)

    return out_to_self


def emit_map_update_for_cartography(
    handler: WebSocketSessionHandler,
    *,
    sd: _SessionData,
    render_id: str,
    player_id: str,
) -> None:
    """Push a ``MAP_UPDATE`` frame to the player's outbound queue when a
    cartography render is dispatched. Mirrors the IMAGE async-emit
    pattern: direct queue push, no journaling, no fan-out via
    ``emit_event``.

    Why no journaling: ``MAP_UPDATE`` is a derived view of world state —
    on reconnect the slice-3 reconnect-replay path will rebuild the
    current map from cartography + ``snapshot.discovered_regions``
    rather than replay every historical frame.

    OTEL: emits ``map.update_emitted`` so the GM panel's "lie detector"
    can confirm the map subsystem actually fired.
    """
    import asyncio

    from sidequest.protocol.enums import MessageType
    from sidequest.protocol.messages import MapUpdateMessage
    from sidequest.server.dispatch.map_update import build_map_update_payload
    from sidequest.server.session_handler import _watcher_publish, logger

    # Resolve the live outbound queue. Mirror of the IMAGE completion
    # path (story 37-30): when room context is bound, the registry's
    # current socket queue survives mid-turn reconnects; otherwise fall
    # back to the legacy out_queue captured at construction.
    target_queue: asyncio.Queue[object] | None = None
    room_slug: str | None = None
    if handler._room is not None:
        room_slug = handler._room.slug
        registry = handler._room_registry
        if registry is not None:
            room = registry.get(room_slug)
            if room is not None:
                socket_id = room.socket_for_player(player_id)
                if socket_id is not None:
                    target_queue = room.queue_for_socket(socket_id)
    if target_queue is None:
        target_queue = handler._out_queue
    if target_queue is None:
        logger.warning(
            "map_update.skipped reason=no_outbound_queue render_id=%s",
            render_id,
        )
        return

    # Pull cartography from the bound world. ``getattr`` chain handles
    # legacy/test fixtures where the world or its cartography may be
    # absent — emit anyway with cartography=None (the wire model allows
    # it) so the UI at least learns the current location.
    world = sd.genre_pack.worlds.get(sd.world_slug) if sd.genre_pack else None
    cartography = getattr(world, "cartography", None) if world is not None else None

    payload = build_map_update_payload(
        snapshot=sd.snapshot,
        cartography=cartography,
    )
    if payload is None:
        # No current location — emitting an empty MAP_UPDATE would make
        # the UI worse, not better. Surface via OTEL so the GM panel
        # can see the skip rather than silently dropping.
        _watcher_publish(
            "state_transition",
            {
                "field": "map",
                "op": "skipped",
                "reason": "no_current_location",
                "render_id": render_id,
                "tier": "cartography",
                "player_id": player_id,
            },
            component="map",
            severity="warning",
        )
        return

    msg = MapUpdateMessage(
        type=MessageType.MAP_UPDATE,  # type: ignore[arg-type]
        payload=payload,
        player_id=player_id,
    )

    try:
        target_queue.put_nowait(msg)
    except asyncio.QueueFull:
        logger.warning("map_update.outbound_queue_full render_id=%s", render_id)
        return

    # OTEL lie-detector — every MAP_UPDATE that hits a queue gets a
    # span. Origin marker mirrors the Rust ``emit_map_update_telemetry``
    # helper so when the location-change and reconnect paths land in
    # slices 2/3, the GM panel can distinguish them at a glance.
    nav_mode = payload.cartography.navigation_mode if payload.cartography else "none"
    _watcher_publish(
        "state_transition",
        {
            "field": "map",
            "op": "update_emitted",
            "origin": "cartography_render",
            "render_id": render_id,
            "tier": "cartography",
            "player_id": player_id,
            "room_slug": room_slug or "",
            "current_location": str(payload.current_location),
            "region": str(payload.region),
            "explored_count": len(payload.explored),
            "has_cartography": payload.cartography is not None,
            "cartography_navigation_mode": nav_mode,
            "genre": sd.genre_slug,
        },
        component="map",
    )
    logger.info(
        "map_update.emitted render_id=%s location=%s explored=%d",
        render_id,
        str(payload.current_location),
        len(payload.explored),
    )


def emit_scrapbook_entry(
    handler: WebSocketSessionHandler,
    *,
    sd: _SessionData,
    snapshot,  # GameSnapshot — avoid circular import in TYPE_CHECKING
    result: object,
) -> None:
    """Persist a scrapbook row + emit a SCRAPBOOK_ENTRY event for one turn.

    Called immediately after the NARRATION emit so the entry's seq lands
    adjacent to its narration in the journal. The IMAGE that may follow
    from the daemon is async — its URL arrives later and the UI gallery
    merges by ``turn_id``. We never block on the daemon here.

    Pure reuse: location from snapshot, excerpt from the narrator's prose,
    NPCs from the orchestrator's structured extraction. No new LLM calls.
    """
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.protocol.messages import ScrapbookEntryNpcRef, ScrapbookEntryPayload
    from sidequest.server.session_handler import (
        _resolve_location_display,
        _watcher_publish,
        logger,
    )

    if not isinstance(result, NarrationTurnResult):
        return

    narration_text = (result.narration or "").strip()
    if not narration_text:
        # The UI requires a non-empty excerpt; skip cleanly when the turn
        # produced no prose (only happens in degraded edge cases).
        return

    # UI contract: ``location`` must be non-empty. Fall back to the raw
    # snapshot location when the display lookup yields nothing — better
    # to surface "Unknown" than to silently drop the entry.
    loc_display = _resolve_location_display(sd.genre_pack, sd.world_slug, snapshot.location) or (
        snapshot.location or "Unknown"
    )

    # Trim the excerpt to a reasonable length for caption rendering. The
    # narrator's full prose lives on the NarrationMessage; the scrapbook
    # caption is a short teaser.
    excerpt = narration_text
    if len(excerpt) > 320:
        excerpt = excerpt[:317].rstrip() + "..."

    # NPCs from the orchestrator's structured extraction — no new
    # inference. ``role`` is the side flag (player/opponent/neutral);
    # ``disposition`` falls back to role when no behavioral string was
    # extracted.
    npc_refs: list[ScrapbookEntryNpcRef] = []
    for mention in result.npcs_present or []:
        name = (getattr(mention, "name", "") or "").strip()
        if not name:
            continue
        role = getattr(mention, "side", "") or "neutral"
        disposition = getattr(mention, "role", "") or role
        npc_refs.append(
            ScrapbookEntryNpcRef(
                name=name,
                role=role,
                disposition=disposition,
            )
        )

    # World facts: lift the narrator's footnote summaries when present.
    world_facts: list[str] = []
    for fn in result.footnotes or []:
        if not isinstance(fn, dict):
            continue
        summary = fn.get("summary") or fn.get("text") or ""
        if isinstance(summary, str) and summary.strip():
            world_facts.append(summary.strip())

    scene_type: str | None = None
    scene_title: str | None = None
    visual = getattr(result, "visual_scene", None)
    if visual is not None:
        tier = (getattr(visual, "tier", None) or "").strip()
        scene_type = tier or None
        subject = (getattr(visual, "subject", None) or "").strip()
        if subject:
            scene_title = subject[:120]

    turn_id = int(snapshot.turn_manager.interaction)

    payload = ScrapbookEntryPayload(
        turn_id=turn_id,
        location=loc_display,
        narrative_excerpt=excerpt,
        scene_title=scene_title,
        scene_type=scene_type,
        image_url=None,  # Async — IMAGE frame follows from the daemon
        world_facts=world_facts,
        npcs_present=npc_refs,
    )

    # Persist to the dedicated scrapbook_entries table — keeps the
    # gallery queryable post-game without walking the events journal.
    try:
        persist_scrapbook_entry(handler, payload)
    except Exception as exc:  # noqa: BLE001 — persistence failure must not block emit
        logger.warning("scrapbook.persist_failed turn=%d error=%s", turn_id, exc)

    # Route through emit_event so the journal gets a row + reconnect
    # replay surfaces prior entries to fresh sockets.
    emit_event(handler, "SCRAPBOOK_ENTRY", payload)

    # OTEL lie-detector: GM panel sees per-turn confirmation that the
    # scrapbook subsystem fired. Without this, regression #2 was
    # invisible for two stories.
    _watcher_publish(
        "state_transition",
        {
            "field": "scrapbook",
            "op": "entry_emitted",
            "turn_id": turn_id,
            "image_url": None,
            "location": loc_display,
            "npc_count": len(npc_refs),
            "world_fact_count": len(world_facts),
            "player_id": sd.player_id,
        },
        component="scrapbook",
    )
