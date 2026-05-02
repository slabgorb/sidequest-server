"""Intent protocol tests — message shape lock."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.protocol.orbital_intent import (
    DrillInIntent,
    DrillOutIntent,
    OrbitalIntent,
    OrbitalIntentResponse,
    ViewMapIntent,
)


def test_view_map_intent_round_trip():
    intent = ViewMapIntent(scope="system_root")
    payload = intent.model_dump()
    assert payload == {"kind": "view_map", "scope": "system_root"}
    parsed = OrbitalIntent.model_validate(payload)
    assert isinstance(parsed.root, ViewMapIntent)


def test_drill_in_intent():
    intent = DrillInIntent(body_id="red_prospect")
    payload = intent.model_dump()
    assert payload == {"kind": "drill_in", "body_id": "red_prospect"}


def test_drill_out_intent():
    intent = DrillOutIntent()
    payload = intent.model_dump()
    assert payload == {"kind": "drill_out"}


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        OrbitalIntent.model_validate({"kind": "explode_sun"})


def test_response_carries_svg():
    resp = OrbitalIntentResponse(
        scope_center="coyote", svg="<svg></svg>", t_hours=0.0
    )
    assert resp.svg.startswith("<svg")
