"""Intent dispatch tests — view_map / drill_in / drill_out cycle."""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.orbital.intent import (
    OrbitalContentUnavailableError,
    handle_orbital_intent,
)
from sidequest.orbital.loader import load_orbital_content
from sidequest.protocol.orbital_intent import OrbitalIntent
from sidequest.server.session import Session

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session_with_world():
    snapshot = GameSnapshot(party_body_id="turning_hub")
    content = load_orbital_content(FIXTURES / "world_minimal")
    return Session(snapshot, orbital_content=content)


@pytest.fixture
def session_no_orbital():
    snapshot = GameSnapshot()
    return Session(snapshot)


def test_view_map_returns_system_scope_svg(session_with_world):
    resp = handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
    )
    assert resp.scope_center == "coyote"
    assert "<svg" in resp.svg or resp.svg.startswith("<?xml")
    assert resp.party_at == "turning_hub"


def test_view_map_with_body_scope_centers_on_body(session_with_world):
    resp = handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "view_map", "scope": "red_prospect"}),
    )
    assert resp.scope_center == "red_prospect"


def test_drill_in_returns_body_scope_svg(session_with_world):
    resp = handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "red_prospect"}),
    )
    assert resp.scope_center == "red_prospect"
    assert 'data-body-id="turning_hub"' in resp.svg


def test_drill_in_unknown_body_raises(session_with_world):
    with pytest.raises(ValueError, match="not in bodies"):
        handle_orbital_intent(
            session_with_world,
            OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "ghost"}),
        )


def test_drill_out_from_body_scope_returns_to_system(session_with_world):
    handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "red_prospect"}),
    )
    resp = handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "drill_out"}),
    )
    assert resp.scope_center == "coyote"


def test_drill_out_at_system_root_is_idempotent(session_with_world):
    resp = handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "drill_out"}),
    )
    assert resp.scope_center == "coyote"


def test_session_without_orbital_content_raises(session_no_orbital):
    with pytest.raises(OrbitalContentUnavailableError):
        handle_orbital_intent(
            session_no_orbital,
            OrbitalIntent.model_validate({"kind": "view_map", "scope": "system_root"}),
        )


def test_intent_persists_scope_on_session(session_with_world):
    handle_orbital_intent(
        session_with_world,
        OrbitalIntent.model_validate({"kind": "drill_in", "body_id": "red_prospect"}),
    )
    assert session_with_world.orbital_scope.center_body_id == "red_prospect"
