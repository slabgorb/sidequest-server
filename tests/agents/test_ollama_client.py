"""Tests for OllamaClient — HTTP backend behind LlmClient (ADR-073 Phase 2)."""
from __future__ import annotations

import asyncio
import json

import pytest

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


class _FakeHttpResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_generate_body(text: str, eval_count: int = 5, prompt_eval_count: int = 7) -> bytes:
    return json.dumps({
        "model": "sidequest-narrator:latest",
        "response": text,
        "done": True,
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
    }).encode()


def test_send_with_model_calls_api_generate_and_maps_tokens():
    calls: list[tuple[str, bytes]] = []

    def fake_http(req):
        calls.append((req.full_url, req.data or b""))
        return _FakeHttpResponse(_ok_generate_body("hello world"))

    client = OllamaClient(http_fn=fake_http)
    response = asyncio.run(client.send_with_model("hi", model="sonnet"))

    assert response.text == "hello world"
    assert response.backend == "ollama"
    assert response.input_tokens == 7
    assert response.output_tokens == 5
    assert response.session_id is None
    assert len(calls) == 1
    url, body_bytes = calls[0]
    assert url == "http://localhost:11434/api/generate"
    body = json.loads(body_bytes)
    assert body["model"] == "sidequest-narrator:latest"
    assert body["prompt"] == "hi"
    assert body["stream"] is False


def test_send_with_model_unknown_hint_raises():
    client = OllamaClient(http_fn=lambda req: pytest.fail("should not call HTTP"))
    with pytest.raises(UnknownModel):
        asyncio.run(client.send_with_model("hi", model="this-model-is-not-mapped"))


def test_send_with_model_non_200_raises_ollama_error():
    def fake_http(req):
        return _FakeHttpResponse(b"server exploded", status=500)

    client = OllamaClient(http_fn=fake_http)
    with pytest.raises(OllamaClientError):
        asyncio.run(client.send_with_model("hi", model="sonnet"))
