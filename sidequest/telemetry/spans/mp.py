"""Multiplayer-lifecycle spans — game creation, slug-connect, seating, pause."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_MP_GAME_CREATED = "mp.game_created"
SPAN_MP_SLUG_CONNECT = "mp.slug_connect"
SPAN_MP_SEAT = "mp.seat"
SPAN_MP_PLAYER_ACTION_PAUSED = "mp.player_action_paused"

FLAT_ONLY_SPANS.update({
    SPAN_MP_GAME_CREATED,
    SPAN_MP_SLUG_CONNECT,
    SPAN_MP_SEAT,
    SPAN_MP_PLAYER_ACTION_PAUSED,
})


@contextmanager
def mp_game_created_span(
    slug: str,
    mode: str,
    genre_slug: str,
    world_slug: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_MP_GAME_CREATED,
        {
            "slug": slug,
            "mode": mode,
            "genre_slug": genre_slug,
            "world_slug": world_slug,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def mp_slug_connect_span(
    slug: str,
    player_id: str,
    mode: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_MP_SLUG_CONNECT,
        {"slug": slug, "player_id": player_id, "mode": mode, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def mp_seat_span(
    slug: str,
    player_id: str,
    character_slot: str | None,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """``character_slot`` may be None for observer seats."""
    with Span.open(
        SPAN_MP_SEAT,
        {
            "slug": slug,
            "player_id": player_id,
            "character_slot": character_slot if character_slot is not None else "",
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def mp_player_action_paused_span(
    slug: str,
    player_id: str,
    absent_player_ids: list[str],
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_MP_PLAYER_ACTION_PAUSED,
        {
            "slug": slug,
            "player_id": player_id,
            "absent_count": len(absent_player_ids),
            "absent_player_ids": ",".join(absent_player_ids),
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span
