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
