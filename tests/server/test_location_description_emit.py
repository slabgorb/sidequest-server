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


def _seed_synthetic_world(tmp_path: Path) -> Path:
    """Build a minimal genre-pack/world dir with one settlement room
    carrying a real entities: block. Returns the genre-pack root so the
    GenreLoader.find monkeypatch can return it.
    """
    genre_root = tmp_path / "test_pack"
    world_dir = genre_root / "worlds" / "test_world"
    rooms = world_dir / "rooms"
    rooms.mkdir(parents=True)
    (rooms / "test_room.yaml").write_text(
        "name: Test Square\n"
        "room_type: settlement\n"
        "description: A well at the centre, lit by a cobwebbed lantern.\n"
        "entities:\n"
        "  - id: square_well\n"
        "    label: the well at the centre\n"
        "    tier: real_object\n"
        "    binding:\n"
        "      kind: location_feature\n"
        "      ref: test_square_well\n"
        "    affordances:\n"
        "      - draw_water\n"
        "  - id: cobwebbed_lantern\n"
        "    label: a cobwebbed lantern\n"
        "    tier: flavor_only\n"
    )
    return genre_root


def _patch_genre_loader_find(monkeypatch, genre_root: Path):
    """Patch GenreLoader.find so the helper resolves world_dir to our tmp tree."""
    from sidequest.genre import loader as loader_mod

    def _fake_find(self, slug):  # noqa: ARG001
        return genre_root

    monkeypatch.setattr(loader_mod.GenreLoader, "find", _fake_find)


def test_emit_sends_message_when_room_has_manifest(tmp_path, monkeypatch):
    """AC-5 + AC-6 production path: room with entities → LocationDescriptionMessage.

    Validates the full transit: GenreLoader.find → load_room_payload →
    typed manifest → LocationDescriptionPayload → LocationDescriptionMessage
    → emit_fn. Uses tmp_path-built content because the live worlds either
    don't use room_graph navigation (beneath_sunden is procedural per
    ADR-106) or don't carry static room YAMLs yet.
    """
    from sidequest.protocol.enums import MessageType
    from sidequest.protocol.messages import LocationDescriptionMessage
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,
    )

    genre_root = _seed_synthetic_world(tmp_path)
    _patch_genre_loader_find(monkeypatch, genre_root)

    emit_fn = MagicMock()
    sd = MagicMock()
    sd.genre_slug = "test_pack"
    sd.world_slug = "test_world"
    sd.player_id = ""
    sd.genre_pack = MagicMock()
    sd.genre_pack.worlds = {"test_world": MagicMock()}
    snapshot = MagicMock()
    snapshot.character_locations = {"alice": "test_room"}

    _maybe_emit_location_description(
        MagicMock(),
        sd=sd,
        snapshot=snapshot,
        actor="alice",
        emit_fn=emit_fn,
    )

    emit_fn.assert_called_once()
    call_args = emit_fn.call_args
    sent_msg = call_args.args[0] if call_args.args else call_args.kwargs.get("msg")
    sent_type = (
        call_args.args[1]
        if len(call_args.args) > 1
        else call_args.kwargs.get("type") or call_args.kwargs.get("msg_type")
    )
    assert sent_type == "LOCATION_DESCRIPTION"
    assert isinstance(sent_msg, LocationDescriptionMessage)
    assert sent_msg.type == MessageType.LOCATION_DESCRIPTION
    assert sent_msg.payload.region_id == "test_room"
    assert len(sent_msg.payload.entities) == 2
    by_id = {e.id: e for e in sent_msg.payload.entities}
    assert by_id["square_well"].tier == "real_object"
    assert by_id["square_well"].binding is not None
    assert by_id["square_well"].binding.kind == "location_feature"
    assert by_id["cobwebbed_lantern"].tier == "flavor_only"
    # AC-8: overlays empty until Story 54-7.
    assert sent_msg.payload.overlays == []


def test_emit_room_id_override_takes_precedence(tmp_path, monkeypatch):
    """AC-5: room_id_override path used by session-resume bypasses actor lookup."""
    from sidequest.server.websocket_session_handler import (
        _maybe_emit_location_description,
    )

    genre_root = _seed_synthetic_world(tmp_path)
    _patch_genre_loader_find(monkeypatch, genre_root)

    emit_fn = MagicMock()
    sd = MagicMock()
    sd.genre_slug = "test_pack"
    sd.world_slug = "test_world"
    sd.player_id = ""
    sd.genre_pack = MagicMock()
    sd.genre_pack.worlds = {"test_world": MagicMock()}
    snapshot = MagicMock()
    # No character_locations — override is what wins.
    snapshot.character_locations = {}

    _maybe_emit_location_description(
        MagicMock(),
        sd=sd,
        snapshot=snapshot,
        actor=None,
        emit_fn=emit_fn,
        room_id_override="test_room",
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
        repo / "sidequest-server" / "sidequest" / "server" / "websocket_session_handler.py"
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
        repo / "sidequest-server" / "sidequest" / "server" / "websocket_session_handler.py"
    )
    handler_src = handler_path.read_text()
    # The resume site uses room_id_override (per plan Task 5 Step 6); the
    # room-change sites do not. Both must exist; this asserts the resume
    # site specifically.
    assert "_maybe_emit_location_description(" in handler_src
    assert "room_id_override=" in handler_src, (
        "session-resume call site must pass room_id_override; see plan Task 5 Step 6"
    )
