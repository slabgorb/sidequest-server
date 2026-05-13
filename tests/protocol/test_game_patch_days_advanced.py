"""Story 50-4 — protocol-layer tests for narrator days_advanced field.

Verifies the narrator's game_patch can emit an integer days_advanced field
that round-trips through extract_structured_from_response extraction.

The extraction function takes a raw narrator response string (with an embedded
```game_patch { ... }``` fence) and returns a dict of structured fields.
"""
import json

from sidequest.agents.orchestrator import extract_structured_from_response


def _raw(patch: dict) -> str:
    """Wrap a patch dict in a game_patch fence for testing."""
    return f"```game_patch\n{json.dumps(patch)}\n```"


def test_days_advanced_field_parses() -> None:
    raw = _raw({"days_advanced": 7})
    result = extract_structured_from_response(raw)
    assert result["days_advanced"] == 7


def test_days_advanced_defaults_zero() -> None:
    raw = _raw({})
    result = extract_structured_from_response(raw)
    assert result["days_advanced"] == 0


def test_days_advanced_rejects_negative() -> None:
    raw = _raw({"days_advanced": -3})
    result = extract_structured_from_response(raw)
    assert result["days_advanced"] == 0  # negative coerced to 0; do not raise


def test_days_advanced_rejects_non_int() -> None:
    raw = _raw({"days_advanced": "seven"})
    result = extract_structured_from_response(raw)
    assert result["days_advanced"] == 0  # silently dropped on type mismatch, like other fields
