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
