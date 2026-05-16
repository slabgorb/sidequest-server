"""Cookbook spans — region-assembly audit (spec §8).

Definitions live here (oq-2). oq-1 calls these helpers at the
materializer seam. The GM panel is the lie detector: every axis roll
is a routed span so Sebastien can verify the cookbook engaged rather
than the narrator improvising.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import SPAN_ROUTES, SpanRoute
from .span import Span

SPAN_COOKBOOK_RACE_ROLLED = "cookbook.race.rolled"
SPAN_COOKBOOK_CR_BAND = "cookbook.cr_band"
SPAN_COOKBOOK_SIZE_BUDGET = "cookbook.size_budget"
SPAN_COOKBOOK_BIGBAD_GATED = "cookbook.bigbad.gated"
SPAN_COOKBOOK_CURATION_DENIED = "cookbook.curation.denied"
SPAN_COOKBOOK_RACE_REROLL = "cookbook.race.reroll"


def _attr(field: str):
    return lambda span, f=field: (span.attributes or {}).get(f)


SPAN_ROUTES[SPAN_COOKBOOK_RACE_ROLLED] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "race",
        "look": _attr("look")(s),
        "race": _attr("race")(s),
        "affinity_weight": _attr("affinity_weight")(s),
        "rng_seed": _attr("rng_seed")(s),
    },
)
SPAN_ROUTES[SPAN_COOKBOOK_CR_BAND] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "cr_band",
        "depth_score": _attr("depth_score")(s),
        "band": _attr("band")(s),
        "cr_min": _attr("cr_min")(s),
        "cr_max": _attr("cr_max")(s),
    },
)
SPAN_ROUTES[SPAN_COOKBOOK_SIZE_BUDGET] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "size_budget",
        "burst_magnitude": _attr("burst_magnitude")(s),
        "wandering_rolls": _attr("wandering_rolls")(s),
        "special_rooms": _attr("special_rooms")(s),
        "loot_rolls": _attr("loot_rolls")(s),
    },
)
SPAN_ROUTES[SPAN_COOKBOOK_BIGBAD_GATED] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "big_bad",
        "depth_score": _attr("depth_score")(s),
        "threshold_crossed": _attr("threshold_crossed")(s),
        "big_bad": _attr("big_bad")(s),
    },
)
SPAN_ROUTES[SPAN_COOKBOOK_CURATION_DENIED] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "curation",
        "race": _attr("race")(s),
        "denied_count": _attr("denied_count")(s),
        "sample_names": _attr("sample_names")(s),
    },
)
SPAN_ROUTES[SPAN_COOKBOOK_RACE_REROLL] = SpanRoute(
    event_type="state_transition",
    component="cookbook",
    extract=lambda s: {
        "field": "race",
        "op": "low_ceiling_reroll",
        "look": _attr("look")(s),
        "band": _attr("band")(s),
        "from_race": _attr("from_race")(s),
        "to_race": _attr("to_race")(s),
        "excluded": _attr("excluded")(s),
    },
)


@contextmanager
def cookbook_race_rolled_span(
    *,
    look: str,
    race: str,
    affinity_weight: float,
    rng_seed: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COOKBOOK_RACE_ROLLED,
        {
            "look": look,
            "race": race,
            "affinity_weight": affinity_weight,
            "rng_seed": rng_seed,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def cookbook_cr_band_span(
    *,
    depth_score: float,
    band: str,
    cr_min: float,
    cr_max: float,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COOKBOOK_CR_BAND,
        {"depth_score": depth_score, "band": band, "cr_min": cr_min, "cr_max": cr_max, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def cookbook_size_budget_span(
    *,
    burst_magnitude: int,
    wandering_rolls: int,
    special_rooms: int,
    loot_rolls: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COOKBOOK_SIZE_BUDGET,
        {
            "burst_magnitude": burst_magnitude,
            "wandering_rolls": wandering_rolls,
            "special_rooms": special_rooms,
            "loot_rolls": loot_rolls,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def cookbook_bigbad_gated_span(
    *,
    depth_score: float,
    threshold_crossed: bool,
    big_bad: str | None,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COOKBOOK_BIGBAD_GATED,
        {
            "depth_score": depth_score,
            "threshold_crossed": threshold_crossed,
            "big_bad": big_bad,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def cookbook_curation_denied_span(
    *,
    race: str,
    denied_count: int,
    sample_names: list[str],
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_COOKBOOK_CURATION_DENIED,
        {"race": race, "denied_count": denied_count, "sample_names": sample_names, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def cookbook_race_reroll_span(
    *,
    look: str,
    band: str,
    from_race: str,
    to_race: str | None,
    excluded: list[str],
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Data-Forced Design Item: a low-ceiling RACE could not fill the
    rolled band; the assembler yielded to another affinity RACE. NOT a
    silent fallback — this span is the GM-panel evidence (spec §7)."""
    with Span.open(
        SPAN_COOKBOOK_RACE_REROLL,
        {
            "look": look,
            "band": band,
            "from_race": from_race,
            "to_race": to_race,
            "excluded": excluded,
            **attrs,
        },
        tracer_override=_tracer,
    ) as span:
        yield span


__all__ = [
    "SPAN_COOKBOOK_BIGBAD_GATED",
    "SPAN_COOKBOOK_CR_BAND",
    "SPAN_COOKBOOK_CURATION_DENIED",
    "SPAN_COOKBOOK_RACE_REROLL",
    "SPAN_COOKBOOK_RACE_ROLLED",
    "SPAN_COOKBOOK_SIZE_BUDGET",
    "cookbook_bigbad_gated_span",
    "cookbook_cr_band_span",
    "cookbook_curation_denied_span",
    "cookbook_race_reroll_span",
    "cookbook_race_rolled_span",
    "cookbook_size_budget_span",
]
