"""Tests for NarratorPerceptionFilter — dispatches per-tool rules."""

from __future__ import annotations

import pytest

from sidequest.agents.narrator_perception_filter import NarratorPerceptionFilter
from sidequest.agents.perception_filter import PerceptionFilter
from sidequest.agents.tool_registry import ToolCategory, ToolResult


@pytest.fixture(autouse=True)
def _isolate_rules():
    """Snapshot _RULES so importing query_character (which registers a rule
    at import time) doesn't leak rule presence to/from other test files.
    """
    from sidequest.agents import narrator_perception_filter as _npf

    snapshot = dict(_npf._RULES)
    try:
        yield
    finally:
        _npf._RULES.clear()
        _npf._RULES.update(snapshot)


def test_filter_conforms_to_protocol() -> None:
    assert isinstance(NarratorPerceptionFilter(), PerceptionFilter)


def test_filter_passes_through_unknown_tool() -> None:
    f = NarratorPerceptionFilter()
    r = ToolResult.ok({"x": 1})
    out = f.filter_result(
        tool_name="brand_new_tool",
        category=ToolCategory.READ,
        result=r,
        perspective_pc="alex",
    )
    assert out.payload == {"x": 1}


def test_filter_passes_through_write_results() -> None:
    f = NarratorPerceptionFilter()
    r = ToolResult.ok({"applied": True})
    out = f.filter_result(
        tool_name="apply_damage",
        category=ToolCategory.WRITE,
        result=r,
        perspective_pc="alex",
    )
    assert out.payload == {"applied": True}


# ---------------------------------------------------------------------------
# query_character rule (Phase C Task 6) — first per-tool perception rule
# ---------------------------------------------------------------------------


def _qc_payload(
    *,
    character_id: str = "Bob",
    name: str = "Bob",
    edge_fraction: float = 0.4,
    stats: dict | None = None,
    inventory: dict | None = None,
    backstory: str | None = None,
    status: list | None = None,
) -> dict:
    p: dict = {
        "character_id": character_id,
        "name": name,
        "race": "Human",
        "char_class": "Delver",
        "pronouns": "they/them",
        "is_friendly": True,
        "edge_current": int(edge_fraction * 10),
        "edge_max": 10,
        "edge_fraction": edge_fraction,
    }
    if stats is not None:
        p["stats"] = stats
    if inventory is not None:
        p["inventory"] = inventory
    if backstory is not None:
        p["backstory"] = backstory
    if status is not None:
        p["status"] = status
    return p


def test_query_character_rule_self_returns_exact() -> None:
    # Importing registers the rule.
    from sidequest.agents.tools import query_character as _qc  # noqa: F401

    f = NarratorPerceptionFilter()
    payload = _qc_payload(character_id="Alice", name="Alice", stats={"str": 12})
    out = f.filter_result(
        tool_name="query_character",
        category=ToolCategory.READ,
        result=ToolResult.ok(payload),
        perspective_pc="Alice",
    )
    assert out.payload == payload  # untouched


def test_query_character_rule_none_perspective_returns_exact() -> None:
    from sidequest.agents.tools import query_character as _qc  # noqa: F401

    f = NarratorPerceptionFilter()
    payload = _qc_payload(stats={"str": 12})
    out = f.filter_result(
        tool_name="query_character",
        category=ToolCategory.READ,
        result=ToolResult.ok(payload),
        perspective_pc=None,
    )
    assert out.payload == payload


def test_query_character_rule_other_pc_coarsens() -> None:
    from sidequest.agents.tools import query_character as _qc  # noqa: F401

    f = NarratorPerceptionFilter()
    payload = _qc_payload(
        character_id="Bob",
        edge_fraction=0.4,  # → bloodied
        stats={"str": 16},
        inventory={"items": [], "gold": 0},
        backstory="secret",
        status=[{"text": "bleeding", "severity": "Wound"}],
    )
    out = f.filter_result(
        tool_name="query_character",
        category=ToolCategory.READ,
        result=ToolResult.ok(payload),
        perspective_pc="Alice",
    )
    coarsened = out.payload
    assert isinstance(coarsened, dict)
    # Identity kept
    assert coarsened["character_id"] == "Bob"
    assert coarsened["name"] == "Bob"
    assert coarsened["is_friendly"] is True
    # Sensitive sections dropped
    assert "stats" not in coarsened
    assert "inventory" not in coarsened
    assert "backstory" not in coarsened
    assert "edge_current" not in coarsened
    assert "edge_max" not in coarsened
    assert "edge_fraction" not in coarsened
    # Status kept; band derived
    assert coarsened["status"] == [{"text": "bleeding", "severity": "Wound"}]
    assert coarsened["edge_band"] == "bloodied"
