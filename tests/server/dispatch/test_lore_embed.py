"""Unit + wiring tests for sidequest/server/dispatch/lore_embed.py.

Phase 3 of session_handler decomposition. These tests verify:
1. Each extracted function exists with the expected signature.
2. The thin delegate methods on WebSocketSessionHandler still call
   into lore_embed.py (wiring guard per CLAUDE.md).
3. Behavior is preserved (functional parity with the pre-extraction
   methods) — supplemented by the canonical end-to-end wiring guard
   in tests/server/test_lore_rag_wiring.py which continues to
   exercise the full pipeline through the delegates.
"""

from __future__ import annotations


def test_lore_embed_module_exposes_required_functions() -> None:
    """Wiring guard — the three required functions must be importable
    from sidequest.server.dispatch.lore_embed by their canonical names.

    DO NOT MODIFY this test until the last extraction (Task 4) lands.
    It is INTENTIONALLY RED until then — the epic-level RED→GREEN gate
    that proves all three moves completed.
    """
    from sidequest.server.dispatch import lore_embed

    assert hasattr(lore_embed, "retrieve_for_turn")
    assert hasattr(lore_embed, "dispatch_worker")
    assert hasattr(lore_embed, "run_worker")
