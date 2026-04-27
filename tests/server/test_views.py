"""Unit + wiring tests for sidequest/server/views.py.

Phase 2 of session_handler decomposition. These tests verify:
1. Each extracted function exists with the expected signature.
2. The thin delegate methods on WebSocketSessionHandler still call
   into views.py (wiring guard per CLAUDE.md).
3. Behavior is preserved (functional parity with the pre-extraction
   methods) — supplemented by the existing integration tests in
   tests/server/test_session_handler_view.py and
   tests/server/test_multiplayer_party_status.py which continue to
   exercise the same code paths through the delegates.
"""

from __future__ import annotations


def test_views_module_exposes_required_functions() -> None:
    """Wiring guard — the seven required functions must be importable
    from sidequest.server.views by their canonical names.

    DO NOT MODIFY this test until the last extraction (Task 8) lands.
    It is INTENTIONALLY RED until then — the epic-level RED→GREEN gate
    that proves all seven moves completed.
    """
    from sidequest.server import views

    assert hasattr(views, "is_hidden_status_list")
    assert hasattr(views, "build_game_state_view")
    assert hasattr(views, "status_effects_by_player")
    assert hasattr(views, "backfill_last_narration_block")
    assert hasattr(views, "party_member_from_character")
    assert hasattr(views, "resolve_self_character")
    assert hasattr(views, "build_session_start_party_status")


def test_is_hidden_status_list_delegate_calls_module_function(monkeypatch) -> None:
    """Wiring guard — WebSocketSessionHandler._is_hidden_status_list
    must delegate to views.is_hidden_status_list."""
    from sidequest.game.status import Status, StatusSeverity
    from sidequest.server import views
    from sidequest.server.session_handler import WebSocketSessionHandler

    captured: list[list[Status]] = []
    sentinel = object()

    def _spy(statuses):
        captured.append(statuses)
        return sentinel

    monkeypatch.setattr(views, "is_hidden_status_list", _spy)

    statuses = [Status(text="hidden", severity=StatusSeverity.Scratch)]
    result = WebSocketSessionHandler._is_hidden_status_list(statuses)

    assert result is sentinel
    assert captured == [statuses]


def test_is_hidden_status_list_matches_hidden_tokens() -> None:
    """Behavioral test — each of the four hidden tokens triggers a True
    result; an unrelated token returns False; an empty list returns False."""
    from sidequest.game.status import Status, StatusSeverity
    from sidequest.server import views

    assert views.is_hidden_status_list([]) is False
    assert views.is_hidden_status_list([Status(text="poisoned", severity=StatusSeverity.Scratch)]) is False
    for token in ("hidden", "invisible", "stealth", "concealed"):
        assert views.is_hidden_status_list([Status(text=token, severity=StatusSeverity.Scratch)]) is True
    # Case-insensitive whole-token (the lowercase comparison is the
    # contract; substring matches are explicitly out of scope per
    # tests/server/test_session_handler_view.py:216).
    assert views.is_hidden_status_list([Status(text="HIDDEN", severity=StatusSeverity.Scratch)]) is True
    assert views.is_hidden_status_list([Status(text="hiddenly", severity=StatusSeverity.Scratch)]) is False


def test_build_game_state_view_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._build_game_state_view must
    delegate to views.build_game_state_view."""
    from sidequest.server import views

    sd, handler = session_handler_factory()
    captured: list[object] = []
    sentinel = object()

    def _spy(h):
        captured.append(h)
        return sentinel

    monkeypatch.setattr(views, "build_game_state_view", _spy)

    result = handler._build_game_state_view()

    assert result is sentinel
    assert captured == [handler]


def test_status_effects_by_player_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler.status_effects_by_player
    must delegate to views.status_effects_by_player."""
    from sidequest.server import views

    sd, handler = session_handler_factory()
    captured: list[object] = []
    sentinel = {"sentinel": ["yes"]}

    def _spy(h):
        captured.append(h)
        return sentinel

    monkeypatch.setattr(views, "status_effects_by_player", _spy)

    result = handler.status_effects_by_player()

    assert result is sentinel
    assert captured == [handler]


def test_backfill_last_narration_block_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._backfill_last_narration_block
    must delegate to views.backfill_last_narration_block."""
    from sidequest.server import views

    sd, handler = session_handler_factory()
    captured: list[tuple] = []
    sentinel: list[object] = []

    def _spy(h, *, player_id):
        captured.append((h, player_id))
        return sentinel

    monkeypatch.setattr(views, "backfill_last_narration_block", _spy)

    result = handler._backfill_last_narration_block(player_id="p:test")

    assert result is sentinel
    assert captured == [(handler, "p:test")]


def test_party_member_from_character_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._party_member_from_character
    must delegate to views.party_member_from_character."""
    from sidequest.server import views

    sd, handler = session_handler_factory()
    sentinel = object()
    captured: list[tuple] = []

    def _spy(h, sd_arg, character, player_id, player_name):
        captured.append((h, sd_arg, character, player_id, player_name))
        return sentinel

    monkeypatch.setattr(views, "party_member_from_character", _spy)

    fake_char = object()
    result = handler._party_member_from_character(sd, fake_char, "p:abc", "Alice")

    assert result is sentinel
    assert captured == [(handler, sd, fake_char, "p:abc", "Alice")]


def test_resolve_self_character_delegate_calls_module_function(
    monkeypatch, session_handler_factory
) -> None:
    """Wiring guard — WebSocketSessionHandler._resolve_self_character
    must delegate to views.resolve_self_character."""
    from sidequest.server import views

    sd, handler = session_handler_factory()
    sentinel = object()
    captured: list[tuple] = []

    def _spy(h, sd_arg):
        captured.append((h, sd_arg))
        return sentinel

    monkeypatch.setattr(views, "resolve_self_character", _spy)

    result = handler._resolve_self_character(sd)

    assert result is sentinel
    assert captured == [(handler, sd)]
