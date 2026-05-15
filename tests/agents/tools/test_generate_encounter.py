"""Tests for the generate_encounter tool — Phase C Task 26.

GENERATE tool. The ``encountergen`` CLI exists at
``sidequest/cli/encountergen/encountergen.py`` with ``generate_enemy(...)``
plus a ``main(argv)`` entry, but its internals require RNG, filesystem
paths, and culture data not threaded through the tool boundary in v1.
The v1 tool reserves the namespace, records the narrator's request in
OTEL (``encountergen_wired=False``), and returns an empty combatants
seed. Tests cover the placeholder path, validator boundaries, optional
hints, and OTEL attribute emission.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from sidequest.agents.narrator_perception_filter import NarratorPerceptionFilter
from sidequest.agents.tool_registry import (
    ToolContext,
    ToolResult,
    ToolResultStatus,
    default_registry,
)
from sidequest.agents.tooling_protocol import ToolUseBlock
from sidequest.agents.tools import generate_encounter as _generate_encounter_module  # noqa: F401
from sidequest.game.persistence import SqliteStore

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _store() -> SqliteStore:
    s = SqliteStore.open_in_memory()
    s.initialize()
    return s


def _make_ctx() -> ToolContext:
    return ToolContext(
        world_id="w",
        session_id="s",
        perspective_pc="Alice",
        turn_number=1,
        store=_store(),
        otel_span=MagicMock(),
        perception_filter=NarratorPerceptionFilter(),
    )


async def _call(arguments: dict, ctx: ToolContext) -> ToolResult:
    registered = default_registry._tools["generate_encounter"]
    args = registered.args_model.model_validate(arguments)
    return await registered.handler(args, ctx)


def _payload(r: ToolResult) -> dict[str, Any]:
    assert r.payload is not None
    return cast(dict[str, Any], r.payload)


def _otel(ctx: ToolContext) -> dict[str, Any]:
    span = cast(MagicMock, ctx.otel_span)
    return {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_generate_encounter_is_registered() -> None:
    assert "generate_encounter" in default_registry.list_names()


# ---------------------------------------------------------------------------
# Happy path — placeholder behaviour
# ---------------------------------------------------------------------------


async def test_happy_path_returns_empty_combatants_with_wired_false() -> None:
    ctx = _make_ctx()
    r = await _call(
        {
            "genre": "low_fantasy",
            "difficulty": 3,
            "terrain": "cavern",
            "theme": "ambush",
        },
        ctx,
    )

    assert r.status is ToolResultStatus.OK
    p = _payload(r)
    assert p["genre"] == "low_fantasy"
    assert p["difficulty"] == 3
    assert p["terrain"] == "cavern"
    assert p["theme"] == "ambush"
    assert p["combatants"] == []
    assert p["encountergen_wired"] is False
    assert "phase e" in p["note"].lower()


async def test_otel_attrs_set_on_happy_path() -> None:
    ctx = _make_ctx()
    await _call(
        {
            "genre": "neon_dystopia",
            "difficulty": 4,
            "terrain": "rooftop",
            "theme": "patrol",
        },
        ctx,
    )

    recorded = _otel(ctx)
    assert recorded["tool.encgen.genre"] == "neon_dystopia"
    assert recorded["tool.encgen.difficulty"] == 4
    assert recorded["tool.encgen.terrain"] == "rooftop"
    assert recorded["tool.encgen.theme"] == "patrol"
    assert recorded["tool.encgen.combatant_count"] == 0
    assert recorded["tool.encgen.encountergen_wired"] is False


async def test_optional_terrain_and_theme_default_to_none_and_otel_attrs_empty() -> None:
    ctx = _make_ctx()
    r = await _call({"genre": "space_opera"}, ctx)

    assert r.status is ToolResultStatus.OK
    p = _payload(r)
    assert p["genre"] == "space_opera"
    # default difficulty=2.
    assert p["difficulty"] == 2
    assert p["terrain"] is None
    assert p["theme"] is None

    recorded = _otel(ctx)
    assert recorded["tool.encgen.terrain"] == ""
    assert recorded["tool.encgen.theme"] == ""
    assert recorded["tool.encgen.genre"] == "space_opera"
    assert recorded["tool.encgen.difficulty"] == 2


# ---------------------------------------------------------------------------
# Difficulty boundary — all five tiers accepted, ge=1/le=5 enforced
# ---------------------------------------------------------------------------


async def test_all_five_difficulty_tiers_accepted() -> None:
    for difficulty in (1, 2, 3, 4, 5):
        ctx = _make_ctx()
        r = await _call({"genre": "low_fantasy", "difficulty": difficulty}, ctx)
        assert r.status is ToolResultStatus.OK
        p = _payload(r)
        assert p["difficulty"] == difficulty


async def test_difficulty_zero_is_validator_error() -> None:
    ctx = _make_ctx()
    out = await default_registry.dispatch(
        ToolUseBlock(
            id="t-diff-zero",
            name="generate_encounter",
            arguments={"genre": "low_fantasy", "difficulty": 0},
        ),
        ctx,
    )
    assert out.is_error is True
    assert "argument validation failed" in out.content


async def test_difficulty_six_is_validator_error() -> None:
    ctx = _make_ctx()
    out = await default_registry.dispatch(
        ToolUseBlock(
            id="t-diff-six",
            name="generate_encounter",
            arguments={"genre": "low_fantasy", "difficulty": 6},
        ),
        ctx,
    )
    assert out.is_error is True
    assert "argument validation failed" in out.content


async def test_empty_genre_is_validator_error() -> None:
    ctx = _make_ctx()
    out = await default_registry.dispatch(
        ToolUseBlock(
            id="t-empty-genre",
            name="generate_encounter",
            arguments={"genre": "", "difficulty": 2},
        ),
        ctx,
    )
    assert out.is_error is True
    assert "argument validation failed" in out.content
