"""ADR-107 — api-contract.md aside-lie guard (RED, story 50-25).

Plan: docs/superpowers/plans/2026-05-17-aside-channel.md Task 8.

The contract currently lies about asides in two contradictory ways
("(not narrated)" AND "broadcast identically to in-character text").
This guard fails until Dev rewrites that section to the true ADR-107
contract, then stays green to keep the lie from regressing.

Path: from sidequest-server/tests/protocol/ the orchestrator root holding
docs/ is parents[3] (protocol -> tests -> sidequest-server -> oq root).
"""

from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "api-contract.md"


def test_api_contract_does_not_lie_about_asides():
    assert CONTRACT.exists()
    text = CONTRACT.read_text(encoding="utf-8")
    # The old contradictory claims must be gone.
    assert "(not narrated)" not in text
    assert "broadcast identically to in-character text" not in text
    # The true contract must be present.
    assert "ASIDE_ANSWER" in text
    assert "no turn" in text.lower() or "non-turn" in text.lower()
