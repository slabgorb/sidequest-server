"""ADR-107 — read-only AsideResolver answer policy (RED, story 50-25).

Plan: docs/superpowers/plans/2026-05-17-aside-channel.md Task 3.
Fails until Dev creates sidequest/agents/aside_resolver.py (GREEN).

The resolver is a GM ruling, not a story beat: it takes a read view +
question, asks the LLM behind a Protocol, and returns a structured
resolution. It has NO write path — "no turn consumed" is enforced by
the object having no hands (test_resolver_has_no_write_surface).
"""

import pytest

from sidequest.agents.aside_resolver import (
    AsideReadView,
    AsideResolution,
    AsideResolver,
)


def _view() -> AsideReadView:
    return AsideReadView(
        character_summary="Hiken, halfling, Small, unencumbered.",
        region_summary="Flooded chamber, standing water ankle-deep for a human.",
        inventory=["torch", "sling"],
        rulebook_summary="Small creatures wade in water rated knee-deep-or-less.",
        recent_narration="The door bursts; black water ankle-deep across the floor.",
    )


class _FakeLLM:
    """Returns whatever JSON the test wires, capturing the system prompt."""

    def __init__(self, payload: str):
        self.payload = payload
        self.seen_system = ""

    async def complete(self, *, system: str, user: str) -> str:
        self.seen_system = system
        return self.payload


@pytest.mark.asyncio
async def test_capability_question_is_answered_and_grounded():
    llm = _FakeLLM(
        '{"answer":"Knee-deep on you, Hiken — wading is slow but fine, no carry.",'
        '"outcome":"answered","grounded_on":["character.size","region.water_depth"]}'
    )
    res = await AsideResolver(llm=llm).resolve(
        question="can I wade or must I be carried?", read_view=_view()
    )
    assert isinstance(res, AsideResolution)
    assert res.outcome == "answered"
    assert res.grounded_on == ("character.size", "region.water_depth")
    assert "wading" in res.answer.lower()


@pytest.mark.asyncio
async def test_hidden_state_question_is_refused():
    llm = _FakeLLM(
        '{"answer":"You\\u0027d have to check — that\\u0027s an action, not a question.",'
        '"outcome":"refused_hidden_state","grounded_on":[]}'
    )
    res = await AsideResolver(llm=llm).resolve(
        question="is the far door trapped?", read_view=_view()
    )
    assert res.outcome == "refused_hidden_state"
    assert res.grounded_on == ()


@pytest.mark.asyncio
async def test_policy_is_in_system_prompt():
    llm = _FakeLLM('{"answer":"x","outcome":"answered","grounded_on":["a"]}')
    await AsideResolver(llm=llm).resolve(question="how does Edge work?", read_view=_view())
    sys = llm.seen_system.lower()
    assert "out-of-character" in sys or "ooc" in sys
    assert "you'd have to check" in sys or "action, not a question" in sys


@pytest.mark.asyncio
async def test_unparseable_llm_output_declines_loudly_not_improvises():
    res = await AsideResolver(llm=_FakeLLM("not json at all")).resolve(
        question="anything", read_view=_view()
    )
    assert res.outcome == "resolver_error"
    assert res.grounded_on == ()
    assert res.answer  # a non-empty loud "ask again" message, never invented lore


@pytest.mark.asyncio
async def test_resolver_has_no_write_surface():
    # Structural guarantee: the resolver exposes only `resolve`. No method
    # name hints at mutation.
    public = [m for m in dir(AsideResolver) if not m.startswith("_")]
    assert public == ["resolve"]
