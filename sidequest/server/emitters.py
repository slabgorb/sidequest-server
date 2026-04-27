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
    handler: "WebSocketSessionHandler",
    payload: "ScrapbookEntryPayload",
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
    handler: "WebSocketSessionHandler",
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
            row = event_log.append_in_transaction(
                kind=kind, payload_json=payload_json, conn=conn
            )
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
