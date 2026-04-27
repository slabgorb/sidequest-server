"""Unit + wiring tests for sidequest/server/emitters.py.

Phase 1 of session_handler decomposition. These tests verify:
1. Each extracted function exists with the expected signature.
2. The thin delegate methods on WebSocketSessionHandler still call
   into emitters.py (wiring guard per CLAUDE.md).
3. Behavior is preserved (functional parity with the pre-extraction
   methods).
"""

from __future__ import annotations


def test_emitters_module_exposes_required_functions() -> None:
    """Wiring guard — the four required functions must be importable
    from sidequest.server.emitters by their canonical names."""
    from sidequest.server import emitters

    assert hasattr(emitters, "persist_scrapbook_entry")
    assert hasattr(emitters, "emit_event")
    assert hasattr(emitters, "emit_map_update_for_cartography")
    assert hasattr(emitters, "emit_scrapbook_entry")


def test_persist_scrapbook_entry_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._persist_scrapbook_entry
    must delegate to emitters.persist_scrapbook_entry."""
    from sidequest.protocol.messages import ScrapbookEntryPayload
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    captured: list[tuple] = []

    def _spy(h, payload):
        captured.append((h, payload))

    monkeypatch.setattr(emitters, "persist_scrapbook_entry", _spy)

    payload = ScrapbookEntryPayload(
        turn_id=1,
        location="test_loc",
        narrative_excerpt="hello",
        scene_title=None,
        scene_type=None,
        image_url=None,
        world_facts=[],
        npcs_present=[],
    )
    handler._persist_scrapbook_entry(payload)

    assert captured == [(handler, payload)]


def test_persist_scrapbook_entry_inserts_row(session_handler_factory) -> None:
    """Behavioral test — calling the function inserts a row into the
    scrapbook_entries table that can be read back."""
    from sidequest.game.event_log import EventLog
    from sidequest.protocol.messages import ScrapbookEntryNpcRef, ScrapbookEntryPayload
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    # The factory does not seed an EventLog by default (legacy path);
    # attach one so the function has a store to write to.
    handler._event_log = EventLog(sd.store)

    payload = ScrapbookEntryPayload(
        turn_id=42,
        location="test_loc",
        narrative_excerpt="The fighter pondered.",
        scene_title="A pondering",
        scene_type="character",
        image_url=None,
        world_facts=["a fact"],
        npcs_present=[
            ScrapbookEntryNpcRef(name="Goblin", role="opponent", disposition="hostile"),
        ],
    )

    emitters.persist_scrapbook_entry(handler, payload)

    rows = sd.store._conn.execute(
        "SELECT turn_id, location, narrative_excerpt FROM scrapbook_entries"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 42
    assert rows[0][1] == "test_loc"
    assert rows[0][2] == "The fighter pondered."


def test_persist_scrapbook_entry_legacy_path_no_event_log_is_noop(
    session_handler_factory,
) -> None:
    """Behavioral test — when handler._event_log is None (legacy path),
    the function returns cleanly without writing or raising."""
    from sidequest.protocol.messages import ScrapbookEntryPayload
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    handler._event_log = None  # legacy path

    payload = ScrapbookEntryPayload(
        turn_id=1,
        location="test_loc",
        narrative_excerpt="nope",
        scene_title=None,
        scene_type=None,
        image_url=None,
        world_facts=[],
        npcs_present=[],
    )

    # Must not raise.
    emitters.persist_scrapbook_entry(handler, payload)


def test_emit_event_delegate_calls_module_function(monkeypatch, session_handler_factory) -> None:
    """Wiring guard — WebSocketSessionHandler._emit_event must delegate
    to emitters.emit_event."""
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    sentinel = object()
    captured: list[tuple] = []

    def _spy(h, kind, payload):
        captured.append((h, kind, payload))
        return sentinel

    monkeypatch.setattr(emitters, "emit_event", _spy)

    result = handler._emit_event("NARRATION", object())

    assert result is sentinel
    assert len(captured) == 1
    assert captured[0][0] is handler
    assert captured[0][1] == "NARRATION"


def test_emit_map_update_for_cartography_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._emit_map_update_for_cartography
    must delegate to emitters.emit_map_update_for_cartography."""
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    captured: list[tuple] = []

    def _spy(h, *, sd, render_id, player_id):
        captured.append((h, sd, render_id, player_id))

    monkeypatch.setattr(emitters, "emit_map_update_for_cartography", _spy)

    handler._emit_map_update_for_cartography(sd=sd, render_id="render-1", player_id=sd.player_id)

    assert captured == [(handler, sd, "render-1", sd.player_id)]


def test_emit_scrapbook_entry_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._emit_scrapbook_entry
    must delegate to emitters.emit_scrapbook_entry."""
    from sidequest.game.session import GameSnapshot
    from sidequest.server import emitters

    sd, handler = session_handler_factory()
    captured: list[tuple] = []

    def _spy(h, *, sd, snapshot, result):
        captured.append((h, sd, snapshot, result))

    monkeypatch.setattr(emitters, "emit_scrapbook_entry", _spy)

    snap = GameSnapshot(genre_slug=sd.genre_slug)
    sentinel_result = object()
    handler._emit_scrapbook_entry(sd=sd, snapshot=snap, result=sentinel_result)

    assert captured == [(handler, sd, snap, sentinel_result)]
