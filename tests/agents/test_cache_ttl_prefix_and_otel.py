"""Cache-TTL cost fix: prerequisite prefix-stability gate + OTEL wiring.

Two guarantees the 1h ephemeral-cache restore depends on:

1. **Prerequisite gate.** The cached system prefix — ``compose_split(agent
   name)[0]``, wrapped verbatim into the single cached ``CacheableBlock``
   at ``Orchestrator._run_narration_turn_sdk`` — MUST be byte-identical
   across sequential turns of one fixed game. A 1h cache write on a
   *mutating* prefix is 2x the base write cost every turn — strictly
   worse than no cache. If this test fails, the fix is invalid: do not
   flip the operative default to 1h.

2. **OTEL wiring.** The ``narration.turn`` cost span must carry
   ``narration.turn.cache_ttl`` so the GM panel can compute write
   amortization (paired with the already-emitted
   ``narration.turn.cache_write_tokens``) and prove the fix engaged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Importing the tools package wires the 26 adapters onto default_registry,
# matching the production SDK path's expectations.
import sidequest.agents.tools  # noqa: F401
from sidequest.agents.anthropic_sdk_client import AnthropicSdkClient
from sidequest.agents.orchestrator import Orchestrator
from tests.agents.fakes.fake_anthropic_sdk_client import (
    FakeAnthropicSdkClient,
    ScriptedResponse,
)


def _end_turn(text: str) -> ScriptedResponse:
    return ScriptedResponse(
        text=text,
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=18,
        cached_input_read_tokens=0,
        cached_input_write_tokens=0,
        model="claude-sonnet-4-6",
    )


@pytest.mark.asyncio
async def test_compose_split_system_prefix_byte_identical_across_3_turns(
    simple_turn_context,
) -> None:
    """PREREQUISITE GATE — the cached prefix does not move turn-to-turn.

    Drives three sequential narration turns through the real prompt
    builder + SDK path with only turn-dynamic inputs changing (action
    text + turn number). The cached block (recorded ``system_blocks[0]``)
    must hash to a single value across all three turns.
    """
    fake = FakeAnthropicSdkClient(
        responses=[_end_turn("turn one"), _end_turn("turn two"), _end_turn("turn three")]
    )
    orch = Orchestrator(client=fake)

    for n in range(3):
        ctx = replace(simple_turn_context, turn_number=n)
        await orch.run_narration_turn(f"player does distinct thing number {n}", ctx)

    assert len(fake.recorded_requests) == 3, (
        f"expected one recorded SDK request per turn; got {len(fake.recorded_requests)}"
    )
    prefixes = [r.system_blocks[0].text for r in fake.recorded_requests]
    digests = {hashlib.sha256(p.encode("utf-8")).hexdigest() for p in prefixes}
    assert len(digests) == 1, (
        "cached system prefix MUST be byte-identical across turns of one "
        f"fixed game; got {len(digests)} distinct prefixes — a 1h cache "
        "write on a mutating prefix is worse than no cache. DO NOT flip "
        "the operative default to 1h until this holds."
    )


# --- OTEL wiring: narration.turn.cache_ttl ---------------------------------


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _TextBlock:
    type: str
    text: str


@dataclass
class _Resp:
    content: list[Any]
    stop_reason: str
    usage: _Usage
    model: str


class _Msgs:
    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _Sdk:
    def __init__(self, responses: list[_Resp]) -> None:
        self.messages = _Msgs(responses)


@pytest.mark.asyncio
async def test_narration_turn_span_carries_cache_ttl(
    simple_turn_context,
    otel_capture: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narration.turn cost span exposes the configured cache TTL so
    the GM panel can prove the 1h fix engaged and compute amortization."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sdk = _Sdk(
        responses=[
            _Resp(
                content=[_TextBlock(type="text", text="The torch sputters.")],
                stop_reason="end_turn",
                usage=_Usage(
                    input_tokens=300,
                    output_tokens=40,
                    cache_read_input_tokens=28000,
                    cache_creation_input_tokens=0,
                ),
                model="claude-sonnet-4-6",
            )
        ]
    )
    client = AnthropicSdkClient(sdk=sdk, cache_ttl="1h")
    orch = Orchestrator(client=client)

    await orch.run_narration_turn("look around", simple_turn_context)

    turn_spans = [s for s in otel_capture.get_finished_spans() if s.name == "narration.turn"]
    assert turn_spans, "expected a narration.turn span"
    attrs = dict(turn_spans[0].attributes or {})
    assert attrs.get("narration.turn.cache_ttl") == "1h", (
        f"narration.turn.cache_ttl should reflect the client's configured "
        f"TTL; got {attrs.get('narration.turn.cache_ttl')!r}"
    )
