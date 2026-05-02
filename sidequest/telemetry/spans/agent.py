"""Agent / LLM call spans — Claude subprocess and turn-LLM pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_AGENT_CALL = "agent.call"
SPAN_AGENT_CALL_SESSION = "agent.call.session"
SPAN_TURN_AGENT_LLM_PROMPT_BUILD = "turn.agent_llm.prompt_build"
SPAN_TURN_AGENT_LLM_INFERENCE = "turn.agent_llm.inference"
SPAN_TURN_AGENT_LLM_PARSE_RESPONSE = "turn.agent_llm.parse_response"

FLAT_ONLY_SPANS.update(
    {
        SPAN_AGENT_CALL,
        SPAN_AGENT_CALL_SESSION,
        SPAN_TURN_AGENT_LLM_PROMPT_BUILD,
        SPAN_TURN_AGENT_LLM_INFERENCE,
        SPAN_TURN_AGENT_LLM_PARSE_RESPONSE,
    }
)


@contextmanager
def agent_call_span(
    model: str,
    prompt_len: int,
    *,
    backend: str = "claude-cli",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_AGENT_CALL,
        {"model": model, "prompt_len": prompt_len, "agent.backend": backend, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def agent_call_session_span(
    model: str,
    prompt_len: int,
    *,
    backend: str = "claude-cli",
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_AGENT_CALL_SESSION,
        {"model": model, "prompt_len": prompt_len, "agent.backend": backend, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def turn_agent_llm_inference_span(
    model: str,
    prompt_len: int,
    *,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    with Span.open(
        SPAN_TURN_AGENT_LLM_INFERENCE,
        {"model": model, "prompt_len": prompt_len, **attrs},
        tracer_override=_tracer,
    ) as span:
        yield span
