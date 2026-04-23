"""Lazy-fill ProjectionCache rows for a newly-joined player.

See spec §Persistence / mid-session join. The filter runs against the
current GameStateView for historical events — a documented softening
of the single-truth invariant to avoid reintroducing the derived-
snapshot store.
"""
from __future__ import annotations

import time

from sidequest.game.event_log import EventLog
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.envelope import MessageEnvelope
from sidequest.game.projection.view import GameStateView
from sidequest.game.projection_filter import ProjectionFilter
from sidequest.telemetry.spans import projection_cache_lazy_fill_span


def lazy_fill(
    *,
    event_log: EventLog,
    cache: ProjectionCache,
    filter_: ProjectionFilter,
    view: GameStateView,
    player_id: str,
) -> int:
    """Fill cache rows for every event this player does not yet have.

    Returns the number of rows filled.
    """
    with projection_cache_lazy_fill_span(player_id=player_id) as span:
        start = time.perf_counter()
        existing = {c.event_seq for c in cache.read_since(player_id=player_id, since_seq=0)}
        filled = 0
        for row in event_log.read_since(since_seq=0):
            if row.seq in existing:
                continue
            envelope = MessageEnvelope(
                kind=row.kind, payload_json=row.payload_json, origin_seq=row.seq
            )
            decision = filter_.project(envelope=envelope, view=view, player_id=player_id)
            cache.write(event_seq=row.seq, player_id=player_id, decision=decision)
            filled += 1
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        span.set_attribute("events_filled", filled)
        span.set_attribute("ms", elapsed_ms)
        return filled
