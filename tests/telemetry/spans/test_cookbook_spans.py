"""Cookbook span definitions exist, are importable, and are routed."""

from __future__ import annotations

from sidequest.telemetry.spans import (
    SPAN_COOKBOOK_BIGBAD_GATED,
    SPAN_COOKBOOK_CR_BAND,
    SPAN_COOKBOOK_CURATION_DENIED,
    SPAN_COOKBOOK_RACE_REROLL,
    SPAN_COOKBOOK_RACE_ROLLED,
    SPAN_COOKBOOK_SIZE_BUDGET,
)
from sidequest.telemetry.spans._core import SPAN_ROUTES


def test_spec_8_spans_named() -> None:
    assert SPAN_COOKBOOK_RACE_ROLLED == "cookbook.race.rolled"
    assert SPAN_COOKBOOK_CR_BAND == "cookbook.cr_band"
    assert SPAN_COOKBOOK_SIZE_BUDGET == "cookbook.size_budget"
    assert SPAN_COOKBOOK_BIGBAD_GATED == "cookbook.bigbad.gated"
    assert SPAN_COOKBOOK_CURATION_DENIED == "cookbook.curation.denied"
    # Data-Forced Design Item: low-ceiling re-roll is observable.
    assert SPAN_COOKBOOK_RACE_REROLL == "cookbook.race.reroll"


def test_spans_are_routed() -> None:
    for name in (
        "cookbook.race.rolled",
        "cookbook.cr_band",
        "cookbook.size_budget",
        "cookbook.bigbad.gated",
        "cookbook.curation.denied",
        "cookbook.race.reroll",
    ):
        assert name in SPAN_ROUTES, f"{name} not registered in SPAN_ROUTES"
