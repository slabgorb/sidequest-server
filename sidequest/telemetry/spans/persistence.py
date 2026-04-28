"""Persistence spans — save/load/delete of SQLite session files."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_PERSISTENCE_SAVE = "persistence_save"
SPAN_PERSISTENCE_LOAD = "persistence_load"
SPAN_PERSISTENCE_DELETE = "persistence_delete"

FLAT_ONLY_SPANS.update({
    SPAN_PERSISTENCE_SAVE,
    SPAN_PERSISTENCE_LOAD,
    SPAN_PERSISTENCE_DELETE,
})


@contextmanager
def persistence_save_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_PERSISTENCE_SAVE,
        {"genre": genre, "world": world, "player": player, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def persistence_load_span(
    genre: str,
    world: str,
    player: str,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_PERSISTENCE_LOAD,
        {"genre": genre, "world": world, "player": player, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
