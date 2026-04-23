"""Tests for narrator-prompt PacingHint wiring on Orchestrator (story 42-3, AC3).

Wires :class:`sidequest.game.tension_tracker.PacingHint` into
:class:`sidequest.agents.orchestrator.TurnContext` and asserts that
``Orchestrator.build_narrator_prompt`` registers a ``"pacing"`` section in
``AttentionZone.Late`` (Rust parity — see
``sidequest-api/crates/sidequest-agents/src/prompt_framework/mod.rs:108``).

Spec deviation logged in session: AC3 / context-story-42-3 says "Early or
Valley" zone; Rust source uses ``Late``. We follow Rust per spec authority
("if Rust places it in Valley, follow Rust"). The existing Python helper
``register_pacing_section`` already uses ``Late`` + section name ``"pacing"``.
"""

from __future__ import annotations

import json

from sidequest.agents.claude_client import ClaudeClient
from sidequest.agents.orchestrator import (
    NarratorPromptTier,
    Orchestrator,
    TurnContext,
)
from sidequest.agents.prompt_framework import (
    AttentionZone,
    PromptRegistry,
    PromptSection,
)
from sidequest.game.tension_tracker import DeliveryMode, PacingHint

# ---------------------------------------------------------------------------
# Minimal canned ClaudeClient — copied small to avoid cross-test coupling.
# ---------------------------------------------------------------------------


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
            "session_id": "sess-pacing-wiring-test",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "total_cost_usd": 0.0,
            "num_turns": 1,
        }
    )

    # ``object`` (rather than ``Any``) keeps the spawn-protocol signature
    # honest: ClaudeClient's spawn_fn accepts heterogeneous env / kwargs
    # shapes, but the test never inspects them. Annotated as ``object``
    # so type-checkers reject attribute access without an explicit cast.
    async def spawn_fn(
        command: str, *args: str, env: object = None, **kwargs: object
    ) -> _FakeProcess:
        return _FakeProcess(stdout=payload)

    return ClaudeClient(spawn_fn=spawn_fn)


def _agent_name(orch: Orchestrator) -> str:
    # Mirror what build_narrator_prompt does internally.
    return orch._narrator.name()  # type: ignore[attr-defined]


def _section(
    registry: PromptRegistry, agent_name: str, section_name: str
) -> PromptSection | None:
    """Locate a registered section by name on the registry, or None.

    Uses the public ``get_sections()`` API rather than reaching into the
    private ``_sections`` dict so tests survive a PromptRegistry storage
    refactor.
    """
    for section in registry.get_sections(agent_name):
        if section.name == section_name:
            return section
    return None


# ---------------------------------------------------------------------------
# AC3 — TurnContext exposes a typed pacing_hint field defaulting to None.
# ---------------------------------------------------------------------------


def test_turn_context_has_pacing_hint_field_default_none():
    ctx = TurnContext()
    assert hasattr(ctx, "pacing_hint"), "TurnContext must declare pacing_hint field"
    assert ctx.pacing_hint is None, "pacing_hint must default to None"


def test_turn_context_accepts_pacing_hint_typed():
    hint = PacingHint(
        drama_weight=0.42,
        target_sentences=3,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=None,
    )
    ctx = TurnContext(pacing_hint=hint)
    assert ctx.pacing_hint is hint


# ---------------------------------------------------------------------------
# AC3 — When pacing_hint is None, no section is registered.
# ---------------------------------------------------------------------------


async def test_pacing_hint_none_does_not_register_section():
    orch = Orchestrator(client=_make_client())
    ctx = TurnContext(character_name="Kael", pacing_hint=None)
    _, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Full
    )
    assert _section(registry, _agent_name(orch), "pacing") is None, (
        "no pacing section should register when TurnContext.pacing_hint is None"
    )


# ---------------------------------------------------------------------------
# AC3 — When pacing_hint is set, a "pacing" section registers in Late zone.
# ---------------------------------------------------------------------------


async def test_pacing_hint_present_registers_pacing_section_in_late_zone():
    orch = Orchestrator(client=_make_client())
    hint = PacingHint(
        drama_weight=0.5,
        target_sentences=3,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=None,
    )
    ctx = TurnContext(character_name="Kael", pacing_hint=hint)
    _, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Full
    )
    section = _section(registry, _agent_name(orch), "pacing")
    assert section is not None, "pacing section must be registered when hint present"
    assert section.zone == AttentionZone.Late, (
        f"pacing zone must be AttentionZone.Late (Rust parity), got {section.zone}"
    )


async def test_pacing_hint_section_content_includes_directive():
    orch = Orchestrator(client=_make_client())
    hint = PacingHint(
        drama_weight=0.5,
        target_sentences=3,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=None,
    )
    expected_directive = hint.narrator_directive()
    ctx = TurnContext(character_name="Kael", pacing_hint=hint)
    prompt, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Full
    )
    section = _section(registry, _agent_name(orch), "pacing")
    assert section is not None
    assert "## Pacing Guidance" in section.content, "header must be present"
    assert expected_directive in section.content, (
        "section content must include hint.narrator_directive() text"
    )
    assert expected_directive in prompt, (
        "rendered prompt must include the pacing directive"
    )


async def test_pacing_hint_escalation_beat_appears_when_set():
    orch = Orchestrator(client=_make_client())
    beat = "The environment shifts — introduce a new element to break the monotony."
    hint = PacingHint(
        drama_weight=0.7,
        target_sentences=4,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=beat,
    )
    ctx = TurnContext(character_name="Kael", pacing_hint=hint)
    _, registry = await orch.build_narrator_prompt(
        "wait", ctx, tier=NarratorPromptTier.Full
    )
    section = _section(registry, _agent_name(orch), "pacing")
    assert section is not None
    assert "## Escalation Beat" in section.content
    assert beat in section.content


async def test_pacing_hint_no_escalation_beat_omits_escalation_block():
    orch = Orchestrator(client=_make_client())
    hint = PacingHint(
        drama_weight=0.4,
        target_sentences=2,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=None,
    )
    ctx = TurnContext(character_name="Kael", pacing_hint=hint)
    _, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Full
    )
    section = _section(registry, _agent_name(orch), "pacing")
    assert section is not None
    assert "## Escalation Beat" not in section.content, (
        "escalation block must be omitted when hint.escalation_beat is None"
    )


# ---------------------------------------------------------------------------
# AC3 — Pacing section also registers on Delta tier (combat can start mid-session).
# ---------------------------------------------------------------------------


async def test_pacing_hint_registers_on_delta_tier():
    orch = Orchestrator(client=_make_client())
    hint = PacingHint(
        drama_weight=0.5,
        target_sentences=3,
        delivery_mode=DeliveryMode.Sentence,
        escalation_beat=None,
    )
    ctx = TurnContext(character_name="Kael", pacing_hint=hint, genre="caverns_and_claudes")
    _, registry = await orch.build_narrator_prompt(
        "look around", ctx, tier=NarratorPromptTier.Delta
    )
    section = _section(registry, _agent_name(orch), "pacing")
    assert section is not None, "pacing must register on Delta tier (per-turn dynamic)"
    assert section.zone == AttentionZone.Late
