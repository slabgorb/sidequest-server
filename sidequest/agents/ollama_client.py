"""Ollama HTTP backend for LlmClient (ADR-073 Phase 2)."""
from __future__ import annotations

from sidequest.agents.claude_client import (
    ClaudeResponse,
    LlmCapabilities,
    LlmClientError,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL_MAP: dict[str, str] = {
    "sonnet": "sidequest-narrator:latest",
    "haiku": "sidequest-decomposer:latest",
    "opus": "sidequest-narrator:latest",
}
OLLAMA_HISTORY_CAP = 32  # exchanges (system + N*2 messages)


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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_map: dict[str, str] = dict(model_map or DEFAULT_MODEL_MAP)
        self._histories: dict[str, list[dict[str, str]]] = {}

    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(
            backend_id="ollama",
            supports_sessions=False,
            supports_tools=False,
            max_context_tokens=16_384,
            supports_streaming=False,
        )

    async def send_with_model(self, prompt: str, model: str) -> ClaudeResponse:  # noqa: D401
        raise NotImplementedError("wired in Task 7")

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
