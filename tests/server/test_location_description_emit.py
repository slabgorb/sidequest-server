"""Wiring test: LOCATION_DESCRIPTION fires on room change + session resume.

Story 54-2 / ADR-109. The integration test required by CLAUDE.md
"Every test suite needs a wiring test" — proves _maybe_emit_location_description
has a non-test caller in production code and emits the right shape.

Covers AC-5 (helper exists, graceful absence on missing source), AC-6
(wiring assertions: non-test caller + real fixture round-trip),
AC-8 (overlays array is []).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_emit_helper_is_importable():
    """AC-5: the helper must exist on websocket_session_handler.

    Pre-test for the more involved wiring checks. If the function is
    missing the import fails fast with a clear message.
    """
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,  # noqa: F401
    )


def test_emit_skips_when_no_room_id():
    """AC-5 graceful absence: no current room → no emit, no error."""
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,
    )

    emit_fn = MagicMock()
    sd = MagicMock()
    sd.genre_slug = "caverns_and_claudes"
    sd.world_slug = "caverns_sunden"
    sd.player_id = ""
    # Empty character_locations means no actor has a room yet.
    snapshot = MagicMock()
    snapshot.character_locations = {}

    _maybe_emit_location_description(
        MagicMock(),
        sd=sd,
        snapshot=snapshot,
        actor="alice",
        emit_fn=emit_fn,
    )
    emit_fn.assert_not_called()


def test_emit_sends_message_when_room_has_manifest():
    """AC-5 + AC-6 production path: room with entities → LocationDescriptionMessage.

    Uses the real load_room_payload + the sunden_square fixture seeded by
    plan Task 3. Validates the full transit: loader → typed manifest →
    LocationDescriptionPayload → LocationDescriptionMessage → emit_fn.
    """
    from sidequest.protocol.enums import MessageType
    from sidequest.protocol.messages import LocationDescriptionMessage
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,
    )

    here = Path(__file__).resolve()
    repo = here.parents[3]
    world_dir = (
        repo
        / "sidequest-content"
        / "genre_packs"
        / "caverns_and_claudes"
        / "worlds"
        / "caverns_sunden"
    )
    if not (world_dir / "rooms" / "sunden_square.yaml").exists():
        pytest.skip(
            "sunden_square.yaml fixture not present — Dev should seed per plan Task 3"
        )

    # Build a minimal SessionData stand-in. The helper only needs:
    # - sd.genre_slug, sd.world_slug
    # - sd.player_id
    # - sd.genre_pack.worlds[world_slug] (truthy)
    # - snapshot.character_locations[actor]
    emit_fn = MagicMock()
    sd = MagicMock()
    sd.genre_slug = "caverns_and_claudes"
    sd.world_slug = "caverns_sunden"
    sd.player_id = ""
    sd.genre_pack = MagicMock()
    sd.genre_pack.worlds = {"caverns_sunden": MagicMock()}
    snapshot = MagicMock()
    snapshot.character_locations = {"alice": "sunden_square"}

    _maybe_emit_location_description(
        MagicMock(),
        sd=sd,
        snapshot=snapshot,
        actor="alice",
        emit_fn=emit_fn,
    )

    emit_fn.assert_called_once()
    call_args = emit_fn.call_args
    # emit_fn is invoked as emit_fn(msg, "LOCATION_DESCRIPTION") per plan;
    # accept either positional or keyword call shapes.
    sent_msg = call_args.args[0] if call_args.args else call_args.kwargs.get("msg")
    sent_type = (
        call_args.args[1]
        if len(call_args.args) > 1
        else call_args.kwargs.get("type") or call_args.kwargs.get("msg_type")
    )
    assert sent_type == "LOCATION_DESCRIPTION", (
        f"emit_fn must be called with type tag 'LOCATION_DESCRIPTION'; got {sent_type!r}"
    )
    assert isinstance(sent_msg, LocationDescriptionMessage)
    assert sent_msg.type == MessageType.LOCATION_DESCRIPTION
    assert sent_msg.payload.region_id == "sunden_square"
    assert len(sent_msg.payload.entities) >= 1, (
        "sunden_square fixture must seed at least one entity"
    )
    # AC-8: overlays empty until Story 54-7.
    assert sent_msg.payload.overlays == []


def test_emit_room_id_override_takes_precedence():
    """AC-5: room_id_override path used by session-resume bypasses actor lookup."""
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,
    )

    here = Path(__file__).resolve()
    repo = here.parents[3]
    world_dir = (
        repo
        / "sidequest-content"
        / "genre_packs"
        / "caverns_and_claudes"
        / "worlds"
        / "caverns_sunden"
    )
    if not (world_dir / "rooms" / "sunden_square.yaml").exists():
        pytest.skip("sunden_square.yaml fixture not present")

    emit_fn = MagicMock()
    sd = MagicMock()
    sd.genre_slug = "caverns_and_claudes"
    sd.world_slug = "caverns_sunden"
    sd.player_id = ""
    sd.genre_pack = MagicMock()
    sd.genre_pack.worlds = {"caverns_sunden": MagicMock()}
    snapshot = MagicMock()
    # No character_locations — override should be what wins.
    snapshot.character_locations = {}

    _maybe_emit_location_description(
        MagicMock(),
        sd=sd,
        snapshot=snapshot,
        actor=None,
        emit_fn=emit_fn,
        room_id_override="sunden_square",
    )

    emit_fn.assert_called_once()


def test_emit_called_from_room_change_dispatch():
    """AC-6 wiring test — proves _maybe_emit_location_description has a non-test caller.

    Per CLAUDE.md 'Verify wiring, not just existence': the function must
    actually be invoked from production dispatch code, not just defined.
    """
    here = Path(__file__).resolve()
    repo = here.parents[3]
    handler_path = (
        repo
        / "sidequest-server"
        / "sidequest"
        / "server"
        / "websocket_session_handler.py"
    )
    handler_src = handler_path.read_text()
    assert "def _maybe_emit_location_description(" in handler_src, (
        "definition missing — Dev hasn't added the helper yet"
    )
    # Definition + at least one production call site.
    call_count = handler_src.count("_maybe_emit_location_description(")
    assert call_count >= 2, (
        "expected definition + at least one production call site; "
        f"found {call_count} mentions in websocket_session_handler.py "
        "(definition = 1 occurrence). Per CLAUDE.md every test suite "
        "needs a wiring test."
    )


def test_emit_called_at_session_resume_path():
    """AC-5: session-resume call site exists with room_id_override.

    Distinguishes the resume call from the room-change call — they pass
    different actor/override args, but both must be present per AC-5.
    """
    here = Path(__file__).resolve()
    repo = here.parents[3]
    handler_path = (
        repo
        / "sidequest-server"
        / "sidequest"
        / "server"
        / "websocket_session_handler.py"
    )
    handler_src = handler_path.read_text()
    # The resume site uses room_id_override (per plan Task 5 Step 6); the
    # room-change sites do not. Both must exist; this asserts the resume
    # site specifically.
    assert "_maybe_emit_location_description(" in handler_src
    assert "room_id_override=" in handler_src, (
        "session-resume call site must pass room_id_override; "
        "see plan Task 5 Step 6"
    )
