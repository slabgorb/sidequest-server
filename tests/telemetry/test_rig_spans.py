"""rig.* span emitters fire with the slice's three constants + correct attrs.

Pattern follows tests/telemetry/test_spans.py — installs an
InMemorySpanExporter on a fresh TracerProvider so emit calls are captured.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


def _fresh_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_rig_span_constants() -> None:
    from sidequest.telemetry.spans import (
        SPAN_RIG_BOND_EVENT,
        SPAN_RIG_CONFRONTATION_OUTCOME,
        SPAN_RIG_VOICE_REGISTER_CHANGE,
    )

    assert SPAN_RIG_BOND_EVENT == "rig.bond_event"
    assert SPAN_RIG_VOICE_REGISTER_CHANGE == "rig.voice_register_change"
    assert SPAN_RIG_CONFRONTATION_OUTCOME == "rig.confrontation_outcome"


def test_rig_spans_are_flat_only() -> None:
    from sidequest.telemetry.spans import (
        FLAT_ONLY_SPANS,
        SPAN_RIG_BOND_EVENT,
        SPAN_RIG_CONFRONTATION_OUTCOME,
        SPAN_RIG_VOICE_REGISTER_CHANGE,
    )

    assert SPAN_RIG_BOND_EVENT in FLAT_ONLY_SPANS
    assert SPAN_RIG_VOICE_REGISTER_CHANGE in FLAT_ONLY_SPANS
    assert SPAN_RIG_CONFRONTATION_OUTCOME in FLAT_ONLY_SPANS


def test_emit_rig_bond_event_fires_with_attrs(monkeypatch) -> None:
    from sidequest.telemetry import spans as _spans
    from sidequest.telemetry.spans import SPAN_RIG_BOND_EVENT, emit_rig_bond_event

    provider, exporter = _fresh_provider()
    monkeypatch.setattr(_spans, "tracer", lambda: provider.get_tracer("test"))

    emit_rig_bond_event(
        chassis_id="kestrel",
        actor_id="player_character_1",
        side="both",
        delta_character=0.04,
        delta_chassis=0.06,
        tier_character_before="trusted",
        tier_character_after="trusted",
        tier_chassis_before="trusted",
        tier_chassis_after="trusted",
        confrontation_id="the_tea_brew",
        register="intimate",
    )

    finished = exporter.get_finished_spans()
    matching = [s for s in finished if s.name == SPAN_RIG_BOND_EVENT]
    assert len(matching) == 1
    attrs = matching[0].attributes
    assert attrs["chassis_id"] == "kestrel"
    assert attrs["delta_chassis"] == 0.06
    assert attrs["register"] == "intimate"
    assert attrs["confrontation_id"] == "the_tea_brew"


def test_emit_rig_bond_event_handles_none_confrontation_id(monkeypatch) -> None:
    from sidequest.telemetry import spans as _spans
    from sidequest.telemetry.spans import SPAN_RIG_BOND_EVENT, emit_rig_bond_event

    provider, exporter = _fresh_provider()
    monkeypatch.setattr(_spans, "tracer", lambda: provider.get_tracer("test"))

    emit_rig_bond_event(
        chassis_id="kestrel",
        actor_id="player_character_1",
        side="both",
        delta_character=0.0,
        delta_chassis=0.0,
        tier_character_before="trusted",
        tier_character_after="trusted",
        tier_chassis_before="trusted",
        tier_chassis_after="trusted",
        confrontation_id=None,
        register="intimate",
    )

    finished = exporter.get_finished_spans()
    matching = [s for s in finished if s.name == SPAN_RIG_BOND_EVENT]
    assert len(matching) == 1
    # OTEL attrs cannot be None — None confrontation_id should be omitted
    # or coerced to "" (the magic.py precedent uses "").
    assert matching[0].attributes.get("confrontation_id", "") in ("", None)


def test_emit_rig_voice_register_change_fires(monkeypatch) -> None:
    from sidequest.telemetry import spans as _spans
    from sidequest.telemetry.spans import (
        SPAN_RIG_VOICE_REGISTER_CHANGE,
        emit_rig_voice_register_change,
    )

    provider, exporter = _fresh_provider()
    monkeypatch.setattr(_spans, "tracer", lambda: provider.get_tracer("test"))

    emit_rig_voice_register_change(
        chassis_id="kestrel",
        actor_id="player_character_1",
        register_before="trusted",
        register_after="fused",
        triggering_event="the_tea_brew",
    )

    finished = exporter.get_finished_spans()
    matching = [s for s in finished if s.name == SPAN_RIG_VOICE_REGISTER_CHANGE]
    assert len(matching) == 1
    assert matching[0].attributes["register_after"] == "fused"


def test_emit_rig_confrontation_outcome_fires(monkeypatch) -> None:
    from sidequest.telemetry import spans as _spans
    from sidequest.telemetry.spans import (
        SPAN_RIG_CONFRONTATION_OUTCOME,
        emit_rig_confrontation_outcome,
    )

    provider, exporter = _fresh_provider()
    monkeypatch.setattr(_spans, "tracer", lambda: provider.get_tracer("test"))

    emit_rig_confrontation_outcome(
        chassis_id="kestrel",
        confrontation_id="the_tea_brew",
        register="intimate",
        branch="clear_win",
        outputs=["bond_strength_growth_via_intimacy", "chassis_lineage_intimate"],
    )

    finished = exporter.get_finished_spans()
    matching = [s for s in finished if s.name == SPAN_RIG_CONFRONTATION_OUTCOME]
    assert len(matching) == 1
    attrs = matching[0].attributes
    assert attrs["branch"] == "clear_win"
    # outputs is a list of primitives — OTEL allows list[str] as attribute
    assert "bond_strength_growth_via_intimacy" in attrs["outputs"]
