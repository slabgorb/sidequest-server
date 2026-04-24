"""Ollama HTTP backend for LlmClient (ADR-073 Phase 2)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen

from sidequest.agents.claude_client import (
    ClaudeResponse,
    LlmCapabilities,
    LlmClientError,
)
from sidequest.telemetry.spans import agent_call_span

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL_MAP: dict[str, str] = {
    "sonnet": "sidequest-narrator:latest",
    "haiku": "sidequest-decomposer:latest",
    "opus": "sidequest-narrator:latest",
}
OLLAMA_HISTORY_CAP = 32  # exchanges (system + N*2 messages)

# urllib returns a context manager protocol (HTTPResponse); Any keeps the fake HTTP
# response class in tests compatible without needing to mirror the full type.
HttpFn = Callable[[Request], Any]


def _default_http(req: Request) -> Any:
    return urlopen(req, timeout=120)  # noqa: S310 — fixed localhost Ollama URL


class OllamaClientError(LlmClientError):
    """Base error for OllamaClient."""


class UnknownModel(OllamaClientError):
    """Caller asked for a model hint not present in the Ollama model map."""


class OllamaClient:
    """HTTP client against an Ollama server (ADR-073 Phase 2).

    Sessions are simulated client-side: each session_id maps to an in-process
    chat history, replayed on subsequent send_with_session calls.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model_map: dict[str, str] | None = None,
        http_fn: HttpFn | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_map: dict[str, str] = dict(model_map or DEFAULT_MODEL_MAP)
        self._histories: dict[str, list[dict[str, str]]] = {}
        self._http: HttpFn = http_fn or _default_http

    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(
            backend_id="ollama",
            supports_sessions=False,
            supports_tools=False,
            max_context_tokens=16_384,
            supports_streaming=False,
        )

    def _resolve_model(self, hint: str) -> str:
        resolved = self._model_map.get(hint)
        if resolved is None:
            raise UnknownModel(
                f"model hint {hint!r} not in Ollama model_map "
                f"keys={sorted(self._model_map.keys())}"
            )
        return resolved

    async def send_with_model(self, prompt: str, model: str) -> ClaudeResponse:
        local_model = self._resolve_model(model)
        with agent_call_span(model=local_model, prompt_len=len(prompt), backend="ollama"):
            body = {"model": local_model, "prompt": prompt, "stream": False}
            return await asyncio.to_thread(self._post_generate, body)

    def _post_generate(self, body: dict[str, object]) -> ClaudeResponse:
        req = Request(
            f"{self._base_url}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._http(req) as resp:
                status = getattr(resp, "status", 200)
                payload = resp.read()
        except Exception as exc:
            raise OllamaClientError(
                f"ollama /api/generate transport error: {exc}"
            ) from exc
        if status != 200:
            raise OllamaClientError(
                f"ollama /api/generate HTTP {status}: {payload!r:.200}"
            )
        try:
            envelope = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OllamaClientError(
                f"ollama /api/generate non-json body: {exc}"
            ) from exc
        return ClaudeResponse(
            text=envelope.get("response", ""),
            input_tokens=envelope.get("prompt_eval_count"),
            output_tokens=envelope.get("eval_count"),
            session_id=None,
            backend="ollama",
        )

    async def send_with_session(
        self,
        prompt: str,
        model: str,
        session_id: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ClaudeResponse:
        raise NotImplementedError("wired in Task 8")
