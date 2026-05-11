"""Story 49-1 RED — Recency-zone recent-narrative window in narrator prompt.

ADR-098 dropped the ``claude -p --resume`` flag, removing the high-attention
recent-narration block from the narrator's prompt. Post-098 the narrator
loses prior-turn details because ``narrative_log`` lives in the
``<game_state>`` JSON dump (Valley zone, attention-decayed). 2026-05-11
Glenross playtest surfaced the regression:

- Turn 5 prose names a male patient ("Father", "him").
- Turn 6 invents a female "mother/her" with no state constraint to refuse.
- Prose-only facts ("secateurs on the blotter") drop between turns.

The fix this story drives: a dedicated Recency-zone section
(``recent_narrative_context``) carrying the last K=4 narrative_log entries
rendered as readable prose blocks (NOT JSON), labeled by author/round
prefix — alongside the existing Recency-zone constraints
(``player_action``, ``npc_intro_visual_constraint``,
``confrontation_trigger_constraint``).

These tests assert the FUTURE behavior — they will fail until Dev wires
``TurnContext.recent_narrative_log`` and the orchestrator's
``build_narrator_prompt`` registers the new section + emits the OTEL span.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sidequest.agents.orchestrator import Orchestrator, TurnContext
from sidequest.agents.prompt_framework.types import AttentionZone, SectionCategory
from sidequest.game.session import NarrativeEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(*, round_: int, author: str, content: str) -> NarrativeEntry:
    """Construct a minimal NarrativeEntry for fixtures."""
    return NarrativeEntry(round=round_, author=author, content=content)


def _glenross_gender_flip_log() -> list[NarrativeEntry]:
    """Fixture mirroring the 2026-05-11 Glenross gender-flip scenario.

    Turn 5 prose names a male patient ("Father", "his", "him"). The next
    turn must reference the same patient consistently. The narrator can
    only do this if turn 5's prose rides into the prompt in a high-
    attention zone.
    """
    return [
        _entry(
            round_=3,
            author="Player",
            content="I follow the gardener down the hedge-row.",
        ),
        _entry(
            round_=3,
            author="narrator",
            content=(
                "The gardener leads you past clipped boxwoods to a low stone bench "
                "where an old man is propped, a wool blanket across his lap."
            ),
        ),
        _entry(
            round_=4,
            author="Player",
            content="I kneel beside him and check the wound.",
        ),
        _entry(
            round_=5,
            author="narrator",
            content=(
                "Father lies pale against the linen, his breath shallow. "
                "The secateurs rest on the blotter beside him where the "
                "physician set them down. You meet his eyes; he tries to speak."
            ),
        ),
    ]


def _section_by_name(registry, agent_name: str, name: str):
    """Return the registered PromptSection with the given name, or None."""
    for section in registry.registry(agent_name):
        if section.name == name:
            return section
    return None


# ---------------------------------------------------------------------------
# Section registration / zone / category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_section_registered_when_log_non_empty(
    simple_turn_context_turn_three,
):
    """When ``TurnContext.recent_narrative_log`` is non-empty, the
    orchestrator MUST register a ``recent_narrative_context`` section.

    This is the headline AC #1: a section in the Recency zone alongside
    the existing player_action / npc_intro_visual_constraint /
    confrontation_trigger_constraint sections.
    """
    ctx = replace(
        simple_turn_context_turn_three,
        recent_narrative_log=_glenross_gender_flip_log(),
    )
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("I lean closer.", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None, (
        "recent_narrative_context section was not registered. "
        "Existing Recency-zone neighbours: "
        f"{[s.name for s in registry.registry(orch._narrator.name()) if s.zone == AttentionZone.Recency]}"
    )


@pytest.mark.asyncio
async def test_recent_narrative_section_is_in_recency_zone(
    simple_turn_context_turn_three,
):
    """The whole point of the story: the section must land in
    ``AttentionZone.Recency``, not Valley/Late. Without this it gets the
    same attention-decay treatment that broke continuity in the first
    place (ADR-009 attention-aware prompt zones)."""
    ctx = replace(
        simple_turn_context_turn_three,
        recent_narrative_log=_glenross_gender_flip_log(),
    )
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None
    assert section.zone == AttentionZone.Recency, (
        f"recent_narrative_context registered in {section.zone}, "
        "must be Recency to ride high-attention with player_action."
    )


@pytest.mark.asyncio
async def test_recent_narrative_section_category_is_state(
    simple_turn_context_turn_three,
):
    """The section carries grounding state (prior turns), not a rule.
    Use SectionCategory.State so the dashboard Prompt-tab classification
    groups it alongside other state blocks rather than guardrails."""
    ctx = replace(
        simple_turn_context_turn_three,
        recent_narrative_log=_glenross_gender_flip_log(),
    )
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None
    assert section.category == SectionCategory.State


# ---------------------------------------------------------------------------
# Prose rendering (NOT JSON)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_section_renders_as_prose_not_json(
    simple_turn_context_turn_three,
):
    """AC #2: content is rendered as readable prose blocks (NOT JSON).

    Bug pattern this prevents: the existing path serializes narrative_log
    as ``snapshot.model_dump_json()`` — long ``{"round": 5, "author":
    "narrator", ...}`` blobs. The narrator skims those poorly compared to
    flowing prose. Assert the section contains no JSON key syntax for
    ``NarrativeEntry`` fields.
    """
    log = _glenross_gender_flip_log()
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None

    body = section.content
    # Forbid JSON-shaped entries — these are the markers that the section
    # is dumping raw NarrativeEntry models instead of rendering prose.
    assert '"author":' not in body, "recent_narrative_context contains JSON keys, must be prose"
    assert '"round":' not in body, "recent_narrative_context contains JSON keys, must be prose"
    assert '"content":' not in body, "recent_narrative_context contains JSON keys, must be prose"

    # And — the round-tripped JSON of the log MUST NOT appear verbatim.
    blob = json.dumps([e.model_dump() for e in log])
    assert blob not in body

    # Sanity: the actual prose content of each entry must be present so
    # the section is not silently empty.
    for entry in log:
        assert entry.content in body, (
            f"narrative entry content missing from prose section: {entry.content[:40]!r}"
        )


@pytest.mark.asyncio
async def test_recent_narrative_section_labels_author_and_round(
    simple_turn_context_turn_three,
):
    """AC #2 cont'd: each entry is labeled by author/round prefix so the
    narrator can tell player-input from narrator-output and order by
    round. Without a label the prose blocks bleed into each other and
    speaker attribution drifts (Felix's 71-entry author='narrator'
    pattern from Playtest 3 — Story 45-22)."""
    log = _glenross_gender_flip_log()
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None
    body = section.content.lower()

    # Every entry's author must be visibly attributed.
    assert "player" in body, "recent_narrative_context omits Player author label"
    assert "narrator" in body, "recent_narrative_context omits narrator author label"

    # Every round number that appears in the log must be visible in the
    # prefix so the narrator can read entries in chronological order.
    for round_n in sorted({e.round for e in log}):
        assert str(round_n) in section.content, (
            f"recent_narrative_context omits round={round_n} label; "
            "narrator cannot order entries"
        )


# ---------------------------------------------------------------------------
# Cap / ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_section_caps_at_last_four_entries(
    simple_turn_context_turn_three,
):
    """AC #2: default K=4 (two player turns + two narrator turns).

    When the log is longer, the section MUST contain only the last 4
    entries. Without a cap the section grows unbounded, defeating the
    ADR-098 bounded-prompt invariant (every turn carries the same shape).
    """
    long_log = [
        _entry(round_=i, author=("Player" if i % 2 == 0 else "narrator"), content=f"line {i}")
        for i in range(20)
    ]
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=long_log)
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None
    body = section.content

    # Last 4 entries (rounds 16..19) must be present.
    for i in range(16, 20):
        assert f"line {i}" in body, f"entry round={i} missing — cap dropped it accidentally"

    # Earlier entries (rounds 0..15) must NOT be present.
    for i in range(16):
        assert f"line {i}" not in body, (
            f"entry round={i} leaked through cap — section should hold only last 4"
        )


@pytest.mark.asyncio
async def test_recent_narrative_section_preserves_chronological_order(
    simple_turn_context_turn_three,
):
    """Entries must appear oldest → newest so the narrator reads them in
    the order they happened. Reversed ordering would invert cause and
    effect and is worse than no context at all."""
    log = [
        _entry(round_=1, author="Player", content="alpha-action"),
        _entry(round_=1, author="narrator", content="alpha-response"),
        _entry(round_=2, author="Player", content="bravo-action"),
        _entry(round_=2, author="narrator", content="bravo-response"),
    ]
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("act", ctx)

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None
    body = section.content

    positions = [body.index(e.content) for e in log]
    assert positions == sorted(positions), (
        f"recent_narrative_context entries are out of chronological order: {positions}"
    )


# ---------------------------------------------------------------------------
# Empty log — zero-byte-leak
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_section_skipped_when_log_empty(
    simple_turn_context,
):
    """Zero-byte-leak discipline (matches state_summary pattern at
    orchestrator.py:1309): if there's no recent narrative (turn 0 or
    fresh save), the section must not be registered at all. An empty
    section pollutes the prompt and contradicts ADR-098's bounded-prompt
    invariant.
    """
    assert list(simple_turn_context.recent_narrative_log) == [], (
        "fixture should default to an empty narrative log on turn 0"
    )
    orch = Orchestrator()
    _, registry = await orch.build_narrator_prompt("first action", simple_turn_context)

    assert _section_by_name(registry, orch._narrator.name(), "recent_narrative_context") is None


# ---------------------------------------------------------------------------
# Composed prompt — full wiring test (CLAUDE.md "Every Test Suite Needs a
# Wiring Test")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_context_appears_in_composed_prompt_text(
    simple_turn_context_turn_three,
):
    """Wiring: register_section is necessary but not sufficient. The
    composed ``prompt_text`` returned from build_narrator_prompt MUST
    contain the prose content — proving the section is registered AND
    the registry's compose step is actually emitting it.

    Without this assertion a future refactor could quietly drop the
    section from the composed output and the per-section unit tests
    would still pass."""
    log = _glenross_gender_flip_log()
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    prompt_text, _ = await orch.build_narrator_prompt("I lean closer.", ctx)

    # The prose content of every injected entry must ride into the
    # composed prompt; otherwise the narrator never sees it.
    for entry in log:
        assert entry.content in prompt_text, (
            f"composed prompt text missing entry content: {entry.content[:60]!r}"
        )


# ---------------------------------------------------------------------------
# OTEL span — Sebastien's lie-detector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_context_injected_span_emitted(
    simple_turn_context_turn_three, otel_capture
):
    """AC #4: emit ``recent_narrative_context_injected`` with ``turn_count``
    and ``total_tokens`` attributes.

    Sebastien's GM panel needs the span to tell whether the recency
    injector engaged at all — without it, broken wiring looks identical
    to "no recent narrative this turn". Audit fields are load-bearing.
    """
    log = _glenross_gender_flip_log()
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    await orch.build_narrator_prompt("act", ctx)

    spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "recent_narrative_context_injected"
    ]
    assert len(spans) == 1, (
        f"expected exactly one recent_narrative_context_injected span, got "
        f"{[s.name for s in otel_capture.get_finished_spans()]}"
    )

    attrs = dict(spans[0].attributes or {})
    assert "turn_count" in attrs, "span missing required turn_count attribute"
    assert "total_tokens" in attrs, "span missing required total_tokens attribute"
    assert attrs["turn_count"] == len(log), (
        f"turn_count should equal injected-entry count; got {attrs['turn_count']}"
    )
    # Token estimate is a positive integer for a non-empty injection.
    assert isinstance(attrs["total_tokens"], int)
    assert attrs["total_tokens"] > 0, "total_tokens must be > 0 for a non-empty log"


@pytest.mark.asyncio
async def test_recent_narrative_context_injected_span_fires_with_zero_when_empty(
    simple_turn_context, otel_capture
):
    """OTEL no-op-fire discipline (matches ``room.state_injected``): the
    span fires on EVERY narrator turn — including the empty-log case
    with ``turn_count=0`` — so the GM panel can distinguish "feature
    engaged with nothing to inject" from "feature broken / not wired"."""
    orch = Orchestrator()
    await orch.build_narrator_prompt("first action", simple_turn_context)

    spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "recent_narrative_context_injected"
    ]
    assert len(spans) == 1, (
        "recent_narrative_context_injected must fire even when the log is empty "
        "(Sebastien's lie-detector contract)"
    )
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("turn_count") == 0
    assert attrs.get("total_tokens") == 0


# ---------------------------------------------------------------------------
# Regression: Glenross gender flip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gender_flip_regression_father_appears_in_recent_narrative(
    simple_turn_context_turn_three,
):
    """AC #5: the regression test for the 2026-05-11 Glenross playtest.

    Turn 5 prose names a male patient ("Father", "his", "him"). The
    composed turn-6 prompt MUST surface that male-coded language in a
    high-attention zone so the narrator cannot invent a "mother / her"
    without contradicting visible context.

    This test does NOT call the LLM (RED phase — we cannot prove the
    narrator stops inventing); it proves the precondition: the gender
    cues survive into the prompt where the narrator can see them.
    """
    log = _glenross_gender_flip_log()
    ctx = replace(simple_turn_context_turn_three, recent_narrative_log=log)
    orch = Orchestrator()
    prompt_text, registry = await orch.build_narrator_prompt(
        "I take Father's hand.", ctx
    )

    section = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
    assert section is not None, "recency section missing — gender cues will not reach the narrator"
    assert section.zone == AttentionZone.Recency

    # The male-coded language must appear inside the high-attention
    # section, not just somewhere in the prompt.
    body = section.content
    assert "Father" in body, "turn-5 'Father' missing from recency section"
    assert "his" in body.lower(), "turn-5 'his' missing from recency section"

    # And the prose-only fact ("secateurs on the blotter") must also
    # survive — that's the second regression from the playtest.
    assert "secateurs" in body.lower(), (
        "prose-only fact 'secateurs' missing — the 'set down twice' "
        "regression will recur"
    )


# ---------------------------------------------------------------------------
# Bounded-prompt invariant — ADR-098 cross-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_narrative_section_does_not_blow_bounded_prompt():
    """ADR-098 invariant: prompt size does not grow with turn count.

    The new Recency section is bounded by K=4 entries, so it must not
    cause the bounded-prompt test to regress. Walk 30 simulated turns
    with an ever-growing narrative_log; assert the *recency section
    itself* stays within a fixed envelope rather than scaling with log
    length.
    """
    from unittest.mock import AsyncMock

    from sidequest.agents.claude_client import ClaudeResponse

    section_sizes: list[int] = []

    async def capture(system_prompt: str, user_message: str, **kwargs):
        return ClaudeResponse(text='{"narration":"ok"}', session_id=None)

    client = AsyncMock()
    client.send_stateless = AsyncMock(side_effect=capture)

    orch = Orchestrator(client=client)
    log: list[NarrativeEntry] = []
    for turn_n in range(30):
        log.append(
            _entry(
                round_=turn_n,
                author="Player" if turn_n % 2 == 0 else "narrator",
                content=f"turn {turn_n} prose " + ("x" * 40),
            )
        )
        ctx = TurnContext(
            character_name="Kael",
            genre="caverns_and_claudes",
            turn_number=turn_n,
            recent_narrative_log=list(log),
        )
        _, registry = await orch.build_narrator_prompt(f"turn {turn_n} action", ctx)
        sec = _section_by_name(registry, orch._narrator.name(), "recent_narrative_context")
        if sec is not None:
            section_sizes.append(len(sec.content))

    assert section_sizes, "recency section never registered across 30 turns"
    # Ratio of largest to smallest section must stay tight — the cap
    # holds entries to last K so the envelope is essentially flat.
    ratio = max(section_sizes) / min(section_sizes)
    assert ratio <= 1.5, (
        f"recency section grew unbounded across turns: "
        f"min={min(section_sizes)} max={max(section_sizes)} ratio={ratio:.2f}"
    )
