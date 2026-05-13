"""Wiring tests for the AVAILABLE ENCOUNTER TYPES prompt section.

Playtest 2026-04-25 regression: in space_opera the narrator picked
``combat`` (Firefight) for a starship dogfight even though the genre's
``rules.yaml`` declares ``ship_combat`` (vessel scale) and ``dogfight``
side-by-side. Root cause was the narrator-output rule referencing
"AVAILABLE ENCOUNTER TYPES in game_state" — but no section actually
rendered that menu. The narrator was choosing from a closed set it
couldn't see.

Fix: ``TurnContext.available_confrontations`` carries the genre's full
menu of ``(type, label, category)`` triples; ``build_narrator_prompt``
renders it as a ``narrator_available_confrontations`` section in the
Early zone, suppressed when an encounter is already live (the
encounter-live zone enumerates the active type's beats; alternates
aren't relevant per the narrator rule "Only include on the turn the
encounter STARTS").
"""

from __future__ import annotations

import json

from sidequest.agents.claude_client import ClaudeClient
from sidequest.agents.orchestrator import (
    Orchestrator,
    TurnContext,
)
from sidequest.agents.prompt_framework import PromptRegistry, PromptSection


class _FakeProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout.encode(), b""


def _make_client() -> ClaudeClient:
    payload = json.dumps(
        {
            "type": "result",
            "result": "Narration.",
            "session_id": "sess-confrontation-menu-test",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "total_cost_usd": 0.0,
            "num_turns": 1,
        }
    )

    async def spawn_fn(
        command: str, *args: str, env: object = None, **kwargs: object
    ) -> _FakeProcess:
        return _FakeProcess(stdout=payload)

    return ClaudeClient(spawn_fn=spawn_fn)


def _agent_name(orch: Orchestrator) -> str:
    return orch._narrator.name()  # type: ignore[attr-defined]


def _section(registry: PromptRegistry, agent_name: str, section_name: str) -> PromptSection | None:
    for section in registry.get_sections(agent_name):
        if section.name == section_name:
            return section
    return None


_SPACE_OPERA_MENU: list[tuple[str, str, str]] = [
    ("combat", "Firefight", "combat"),
    ("ship_combat", "Ship Combat", "combat"),
    ("dogfight", "Dogfight", "combat"),
    ("negotiation", "Diplomacy", "social"),
]


# ---------------------------------------------------------------------------
# AC1 — When no encounter is active and the menu is non-empty, the
# AVAILABLE ENCOUNTER TYPES section registers and lists every type.
# ---------------------------------------------------------------------------


async def test_available_confrontations_renders_menu_when_no_encounter() -> None:
    orch = Orchestrator(client=_make_client())
    ctx = TurnContext(
        character_name="Carrot",
        available_confrontations=_SPACE_OPERA_MENU,
    )
    _, registry = await orch.build_narrator_prompt("look around", ctx)
    section = _section(
        registry,
        _agent_name(orch),
        "narrator_available_confrontations",
    )
    assert section is not None, (
        "expected narrator_available_confrontations section when "
        "menu is non-empty and no encounter is active"
    )
    body = section.content
    assert "AVAILABLE ENCOUNTER TYPES" in body
    # All four types and labels must appear.
    for cdef_type, cdef_label, _ in _SPACE_OPERA_MENU:
        assert cdef_type in body, f"type {cdef_type!r} missing from menu"
        assert cdef_label in body, f"label {cdef_label!r} missing from menu"
    # Categories surface so the narrator can disambiguate combat
    # subtypes — the load-bearing fix for the playtest regression.
    assert "category=combat" in body
    assert "category=social" in body


# ---------------------------------------------------------------------------
# AC2 — When an encounter is already live, suppress the menu (the
# encounter-live zone enumerates the active type's beats; alternates
# are noise that could push the narrator to switch types mid-fight).
# ---------------------------------------------------------------------------


async def test_available_confrontations_suppressed_when_encounter_active() -> None:
    orch = Orchestrator(client=_make_client())
    ctx = TurnContext(
        character_name="Carrot",
        in_combat=True,
        in_encounter=True,
        available_confrontations=_SPACE_OPERA_MENU,
    )
    _, registry = await orch.build_narrator_prompt("shoot the corvette", ctx)
    section = _section(
        registry,
        _agent_name(orch),
        "narrator_available_confrontations",
    )
    assert section is None, (
        "menu must NOT register while an encounter is active — the "
        "encounter-live zone covers the active type's beats; menu "
        "would just tempt the narrator to switch types mid-fight"
    )


# ---------------------------------------------------------------------------
# AC3 — Empty menu (legacy / pre-port pack with no rules.confrontations)
# does not register a section. Zero byte leak.
# ---------------------------------------------------------------------------


async def test_available_confrontations_empty_menu_does_not_register() -> None:
    orch = Orchestrator(client=_make_client())
    ctx = TurnContext(character_name="Carrot", available_confrontations=[])
    _, registry = await orch.build_narrator_prompt("look around", ctx)
    assert (
        _section(
            registry,
            _agent_name(orch),
            "narrator_available_confrontations",
        )
        is None
    )


# ---------------------------------------------------------------------------
# AC4 — TurnContext default for the new field is an empty list (no
# byte leak in any code path that doesn't populate it).
# ---------------------------------------------------------------------------


def test_turn_context_available_confrontations_defaults_empty() -> None:
    ctx = TurnContext()
    assert ctx.available_confrontations == []
