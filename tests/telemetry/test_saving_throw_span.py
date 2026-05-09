"""Tests for encounter.saving_throw_resolved OTEL span."""

from sidequest.telemetry.spans._core import SPAN_ROUTES
from sidequest.telemetry.spans.encounter import (
    SPAN_ENCOUNTER_SAVING_THROW_RESOLVED,
    encounter_saving_throw_resolved_span,
)


def test_saving_throw_span_constant_declared():
    assert SPAN_ENCOUNTER_SAVING_THROW_RESOLVED == "encounter.saving_throw_resolved"


def test_saving_throw_span_route_registered():
    assert SPAN_ENCOUNTER_SAVING_THROW_RESOLVED in SPAN_ROUTES
    route = SPAN_ROUTES[SPAN_ENCOUNTER_SAVING_THROW_RESOLVED]
    assert route.event_type == "state_transition"
    assert route.component == "encounter"


def test_saving_throw_span_emits_with_required_attrs():
    with encounter_saving_throw_resolved_span(
        defender_actor="carl",
        defender_class="Mage",
        category="rods_staves_spells",
        ability="WIS",
        threat_label="SLEEP",
        target=15,
        roll=11,
        mod=1,
        total=12,
        shift=-3,
        tier="Fail",
        spell_id="sleep",
        encounter_type="combat",
        mindless_gate=False,
    ):
        pass
