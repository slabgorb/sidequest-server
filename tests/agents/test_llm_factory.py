"""Tests for llm_factory.build_llm_client — env-var backend selection."""

from __future__ import annotations

import pytest

from sidequest.agents.claude_client import ClaudeClient, LlmClient
from sidequest.agents.llm_factory import UnknownBackend, build_llm_client
from sidequest.agents.ollama_client import OllamaClient


def test_default_is_claude(monkeypatch):
    monkeypatch.delenv("SIDEQUEST_LLM_BACKEND", raising=False)
    client = build_llm_client()
    assert isinstance(client, ClaudeClient)
    assert isinstance(client, LlmClient)


def test_explicit_claude(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "claude")
    client = build_llm_client()
    assert isinstance(client, ClaudeClient)


def test_ollama_backend_picks_url_from_env(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "ollama")
    monkeypatch.setenv("SIDEQUEST_OLLAMA_URL", "http://example.local:9000")
    client = build_llm_client()
    assert isinstance(client, OllamaClient)
    assert client._base_url == "http://example.local:9000"


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "gpt4")
    with pytest.raises(UnknownBackend):
        build_llm_client()


def test_whitespace_and_case_insensitivity(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", " CLAUDE  ")
    assert isinstance(build_llm_client(), ClaudeClient)
    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "Ollama")
    assert isinstance(build_llm_client(), OllamaClient)


def test_anthropic_sdk_backend_key_routes_to_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidequest.agents.anthropic_sdk_client import AnthropicSdkClient
    from sidequest.agents.llm_factory import build_llm_client

    monkeypatch.setenv("SIDEQUEST_LLM_BACKEND", "anthropic_sdk")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = build_llm_client()
    assert isinstance(client, AnthropicSdkClient)


def test_default_is_still_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    from sidequest.agents.claude_client import ClaudeClient
    from sidequest.agents.llm_factory import build_llm_client

    monkeypatch.delenv("SIDEQUEST_LLM_BACKEND", raising=False)
    client = build_llm_client()
    assert isinstance(client, ClaudeClient)
