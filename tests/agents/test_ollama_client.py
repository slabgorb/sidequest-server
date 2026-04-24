"""Tests for OllamaClient — HTTP backend behind LlmClient (ADR-073 Phase 2)."""
from __future__ import annotations

from sidequest.agents.claude_client import LlmClient
from sidequest.agents.ollama_client import OllamaClient, OllamaClientError, UnknownModel


def test_ollama_client_satisfies_llm_client_protocol():
    client = OllamaClient(base_url="http://localhost:11434")
    assert isinstance(client, LlmClient)


def test_ollama_client_reports_capabilities():
    client = OllamaClient(base_url="http://localhost:11434")
    caps = client.capabilities()
    assert caps.backend_id == "ollama"
    assert caps.supports_sessions is False
    assert caps.supports_tools is False
    assert caps.max_context_tokens == 16_384
    assert caps.supports_streaming is False


def test_unknown_model_is_ollama_client_error_subclass():
    assert issubclass(UnknownModel, OllamaClientError)
