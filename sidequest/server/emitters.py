"""Event emission helpers extracted from WebSocketSessionHandler.

Phase 1 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes `handler: WebSocketSessionHandler` as its first
argument and operates on the handler's mutable state. No new abstractions
introduced — this is pure extraction with byte-identical behavior to the
original methods on WebSocketSessionHandler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sidequest.agents.perception_rewriter import rewrite_for_recipient

if TYPE_CHECKING:
    from sidequest.protocol.messages import ScrapbookEntryPayload
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData

logger = logging.getLogger(__name__)


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
            " narrative_excerpt, world_facts, npcs_present, render_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload.turn_id,
                payload.scene_title,
                payload.scene_type,
                payload.location,
                payload.image_url,
                payload.narrative_excerpt,
                facts_json,
                npcs_json,
                payload.render_status,
            ),
        )


def update_scrapbook_image_url(
    handler: WebSocketSessionHandler,
    turn_id: int,
    image_url: str,
) -> bool:
    """Backfill the ``image_url`` for the most recent scrapbook entry at
    ``turn_id``. Returns ``True`` when a row was updated, ``False`` when
    no matching row exists yet (rare — would mean render.completed
    arrived before the SCRAPBOOK_ENTRY emit, which we never dispatch in
    that order) or when the handler has no event log (legacy non-slug
    path).

    Playtest 2026-05-02: scrapbook state vanishes on browser reload
    because the IMAGE message is broadcast live but never persisted, so
    `slug_connect.replay` rebuilds SCRAPBOOK_ENTRY events with their
    original ``image_url=None`` payload. Updating the table here lets
    the replay path JOIN and inject the URL into rebuilt SCRAPBOOK_ENTRY
    payloads (see `connect.py:_inject_scrapbook_image_urls`).

    Multiple scrapbook rows can share a turn_id in principle (the table
    has no UNIQUE constraint), but in practice the narrator emits one
    entry per turn. We update by ``rowid DESC LIMIT 1`` for the given
    turn — the most recent entry — since render.completed arrives
    after that turn's emit.
    """
    if handler._event_log is None:
        return False
    if not image_url:
        return False
    store = handler._event_log.store
    try:
        with store._conn:
            cur = store._conn.execute(
                "UPDATE scrapbook_entries SET image_url = ? "
                "WHERE rowid = ("
                "  SELECT rowid FROM scrapbook_entries "
                "  WHERE turn_id = ? AND image_url IS NULL "
                "  ORDER BY rowid DESC LIMIT 1"
                ")",
                (image_url, turn_id),
            )
            return cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001 — render path must not crash on a backfill miss
        logger.warning(
            "scrapbook.image_url_update_failed turn_id=%d error=%s",
            turn_id,
            exc,
        )
        return False


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
                from sidequest.server import views

                view = views.build_game_state_view(handler)
                envelope = MessageEnvelope(
                    kind=row.kind,
                    payload_json=row.payload_json,
                    origin_seq=row.seq,
                )
                # G6: status-effect perception overlay. Built once per
                # event (not per recipient) — snapshot statuses don't
                # change mid-fanout.
                status_effects = views.status_effects_by_player(handler)

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


def emit_scrapbook_entry(
    handler: WebSocketSessionHandler,
    *,
    sd: _SessionData,
    snapshot,  # GameSnapshot — avoid circular import in TYPE_CHECKING
    result: object,
    render_status: str = "rendered",
) -> None:
    """Persist a scrapbook row + emit a SCRAPBOOK_ENTRY event for one turn.

    Called immediately after the NARRATION emit so the entry's seq lands
    adjacent to its narration in the journal. The IMAGE that may follow
    from the daemon is async — its URL arrives later and the UI gallery
    merges by ``turn_id``. We never block on the daemon here.

    Pure reuse: location from snapshot, excerpt from the narrator's prose,
    NPCs from the orchestrator's structured extraction. No new LLM calls.

    ``render_status`` (Story 45-30): the trigger-policy outcome for this
    turn — ``rendered`` (policy fired and dispatch proceeded),
    ``skipped_policy`` (banter / no narrative weight) or ``failed`` (policy
    fired but daemon refused synchronously). The UI uses this to render
    distinct affordances per outcome.
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
        render_status=render_status,
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


async def broadcast_delta(
    *,
    turn_id: str,
    chunk: str,
    seq: int,
    room: object,
) -> None:
    """Broadcast an ephemeral narration delta to all sockets in the room.

    Does NOT call emit_event(). Does NOT touch the projection cache.
    Does NOT run perception_rewriter. Pure presentation channel.

    Room API used (matching SessionRoom):
      room.connected_player_ids() -> list[str]
      room.socket_for_player(pid) -> str | None
      room.queue_for_socket(socket_id) -> asyncio.Queue | None
      queue.put_nowait(msg)

    Per-socket errors (missing socket_id or queue) are logged and skipped so
    one dead/absent player cannot block fan-out to the rest.

    Implementation note: each player gets the same payload — deltas are not
    per-recipient filtered. This is correct today because the perception
    rewriter is a no-op for narration (no kind-tagged spans yet, see
    perception_rewriter.py docstring re: G10 deferral). When G10 ships,
    this fan-out needs revisiting.
    """
    from sidequest.protocol.messages import NarrationDelta, NarrationDeltaPayload

    msg = NarrationDelta(
        payload=NarrationDeltaPayload(
            turn_id=turn_id,
            chunk=chunk,
            seq=seq,
        )
    )
    for pid in room.connected_player_ids():
        socket_id = room.socket_for_player(pid)
        if socket_id is None:
            logger.warning(
                "broadcast_delta.no_socket turn_id=%s seq=%d player_id=%s",
                turn_id,
                seq,
                pid,
            )
            continue
        queue = room.queue_for_socket(socket_id)
        if queue is None:
            logger.warning(
                "broadcast_delta.no_queue turn_id=%s seq=%d player_id=%s socket_id=%s",
                turn_id,
                seq,
                pid,
                socket_id,
            )
            continue
        try:
            queue.put_nowait(msg)
        except Exception:
            # Per-socket errors must not break fan-out to other recipients.
            logger.warning(
                "broadcast_delta.enqueue_failed turn_id=%s seq=%d player_id=%s",
                turn_id,
                seq,
                pid,
            )
