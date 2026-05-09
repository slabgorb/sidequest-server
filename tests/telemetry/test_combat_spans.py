"""Tests for combat OTEL spans: beat_filter route + morale_check.

Task 12 of C&C B/X class-beats plan. Covers:
- SPAN_CONFRONTATION_BEAT_FILTER promoted from flat-only to routed (encounter.py)
- SPAN_MORALE_CHECK declared and routed (combat.py)
- maybe_check_morale emits the span with required attributes
"""

from __future__ import annotations

import random

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_morale_check_span_constant_declared() -> None:
    from sidequest.telemetry.spans.combat import SPAN_MORALE_CHECK

    assert SPAN_MORALE_CHECK == "confrontation.morale_check"


def test_morale_check_span_route_registered() -> None:
    from sidequest.telemetry.spans._core import SPAN_ROUTES
    from sidequest.telemetry.spans.combat import SPAN_MORALE_CHECK

    route = SPAN_ROUTES.get(SPAN_MORALE_CHECK)
    assert route is not None
    assert route.component == "combat"
    assert route.event_type == "state_transition"


def test_morale_check_route_extract_fields() -> None:
    """SpanRoute.extract returns all required GM-panel fields."""
    from sidequest.telemetry.spans._core import SPAN_ROUTES
    from sidequest.telemetry.spans.combat import SPAN_MORALE_CHECK

    route = SPAN_ROUTES[SPAN_MORALE_CHECK]

    class _FakeSpan:
        name = SPAN_MORALE_CHECK
        attributes = {
            "trigger": "first_blood",
            "score": 8,
            "roll": "3+5",
            "total": 8,
            "outcome": "stay",
            "opponent_side_label": "goblins",
            "mindless_opponents_count": 0,
            "flee_consequence": "chase",
        }

    result = route.extract(_FakeSpan())
    assert result["field"] == "morale_check"
    assert result["trigger"] == "first_blood"
    assert result["score"] == 8
    assert result["roll"] == "3+5"
    assert result["total"] == 8
    assert result["outcome"] == "stay"
    assert result["opponent_side_label"] == "goblins"
    assert result["mindless_opponents_count"] == 0
    assert result["flee_consequence"] == "chase"


def test_beat_filter_span_constant_already_declared() -> None:
    """Task 7 added this; confirm it's still there with the right value."""
    from sidequest.telemetry.spans.encounter import SPAN_CONFRONTATION_BEAT_FILTER

    assert SPAN_CONFRONTATION_BEAT_FILTER == "confrontation.beat_filter"


def test_beat_filter_span_route_registered() -> None:
    """Task 12 promotes beat_filter from flat-only to routed."""
    from sidequest.telemetry.spans._core import FLAT_ONLY_SPANS, SPAN_ROUTES
    from sidequest.telemetry.spans.encounter import SPAN_CONFRONTATION_BEAT_FILTER

    route = SPAN_ROUTES.get(SPAN_CONFRONTATION_BEAT_FILTER)
    assert route is not None, "beat_filter must be routed (not flat-only)"
    assert route.component == "combat"
    assert SPAN_CONFRONTATION_BEAT_FILTER not in FLAT_ONLY_SPANS, (
        "beat_filter must not be in FLAT_ONLY_SPANS after promotion to routed"
    )


def test_beat_filter_route_extract_fields() -> None:
    """SpanRoute.extract for beat_filter surfaces GM-panel-required fields."""
    from sidequest.telemetry.spans._core import SPAN_ROUTES
    from sidequest.telemetry.spans.encounter import SPAN_CONFRONTATION_BEAT_FILTER

    route = SPAN_ROUTES[SPAN_CONFRONTATION_BEAT_FILTER]

    class _FakeSpan:
        name = SPAN_CONFRONTATION_BEAT_FILTER
        attributes = {
            "actor": "Rux",
            "class_name": "Fighter",
            "confrontation_type": "combat",
            "available_beat_ids": "attack,defend",
            "spell_slots_remaining": 0.0,
            "pool_size": 4,
            "filtered_size": 2,
        }

    result = route.extract(_FakeSpan())
    assert result["field"] == "beat_filter"
    assert result["character_class"] == "Fighter"
    assert result["confrontation_type"] == "combat"
    assert result["beat_ids"] == "attack,defend"
    assert result["pool_size"] == 4
    assert result["filtered_size"] == 2


def test_morale_check_span_emits_flee_outcome() -> None:
    """maybe_check_morale fires a span with required attrs on flee."""
    from sidequest.game.morale import (
        MoraleOutcome,
        OpponentSideState,
        OpponentState,
        maybe_check_morale,
    )
    from sidequest.genre.models.rules import (
        BeatDef,
        BeatKind,
        ConfrontationDef,
        FleeConsequence,
        MetricDef,
        MoraleDef,
        MoraleTrigger,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")

    import sidequest.game.morale as morale_mod

    original = morale_mod._tracer
    morale_mod._tracer = test_tracer
    try:
        cd = ConfrontationDef(
            type="combat",
            label="C",
            category="combat",
            player_metric=MetricDef(name="m", starting=0, threshold=7),
            opponent_metric=MetricDef(name="m", starting=0, threshold=7),
            beats=[BeatDef(id="attack", label="A", kind=BeatKind.strike, stat_check="STR")],
            morale=MoraleDef(
                score=2,  # almost always fails -> flee
                triggers=[MoraleTrigger.first_blood],
                flee_consequence=FleeConsequence.chase,
            ),
        )
        side = OpponentSideState(label="goblins", opponents=[OpponentState(id="g1")])
        outcome = maybe_check_morale(cd, side, MoraleTrigger.first_blood, random.Random(42))
    finally:
        morale_mod._tracer = original

    assert outcome is MoraleOutcome.flee
    [span] = exporter.get_finished_spans()
    assert span.name == "confrontation.morale_check"
    assert span.attributes["trigger"] == "first_blood"
    assert span.attributes["opponent_side_label"] == "goblins"
    assert span.attributes["outcome"] == "flee"
    assert span.attributes["flee_consequence"] == "chase"
    assert "score" in span.attributes
    assert "roll" in span.attributes
    assert "total" in span.attributes


def test_morale_check_span_emits_stay_when_no_morale_block() -> None:
    """Span is emitted even when morale block is absent (no-op stay path)."""
    from sidequest.game.morale import (
        MoraleOutcome,
        OpponentSideState,
        OpponentState,
        maybe_check_morale,
    )
    from sidequest.genre.models.rules import (
        BeatDef,
        BeatKind,
        ConfrontationDef,
        MetricDef,
        MoraleTrigger,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = provider.get_tracer("test")

    import sidequest.game.morale as morale_mod

    original = morale_mod._tracer
    morale_mod._tracer = test_tracer
    try:
        cd = ConfrontationDef(
            type="combat",
            label="C",
            category="combat",
            player_metric=MetricDef(name="m", starting=0, threshold=7),
            opponent_metric=MetricDef(name="m", starting=0, threshold=7),
            beats=[BeatDef(id="attack", label="A", kind=BeatKind.strike, stat_check="STR")],
            morale=None,
        )
        side = OpponentSideState(label="bandits", opponents=[OpponentState(id="b1")])
        outcome = maybe_check_morale(cd, side, MoraleTrigger.first_blood, random.Random(0))
    finally:
        morale_mod._tracer = original

    assert outcome is MoraleOutcome.stay
    [span] = exporter.get_finished_spans()
    assert span.name == "confrontation.morale_check"
    assert span.attributes["outcome"] == "stay"
    assert span.attributes["trigger"] == "first_blood"
