"""Tests for AnthropicSdkClient — construction, auth, error semantics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidequest.agents.anthropic_sdk_client import (
    AnthropicSdkClient,
    AnthropicSdkConfigError,
)
from sidequest.agents.tooling_protocol import ToolingLlmClient


def test_construction_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicSdkConfigError):
        AnthropicSdkClient()


def test_construction_reads_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-1")
    client = AnthropicSdkClient()
    assert client.api_key_present is True


def test_construction_accepts_explicit_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_sdk = MagicMock(name="AsyncAnthropic")
    client = AnthropicSdkClient(sdk=fake_sdk)
    assert client.api_key_present is False  # bypassed via explicit injection
    assert client._sdk is fake_sdk  # type: ignore[attr-defined]


def test_implements_tooling_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-1")
    client = AnthropicSdkClient()
    assert isinstance(client, ToolingLlmClient)


def test_default_cache_ttl_is_5_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-1")
    client = AnthropicSdkClient()
    assert client.cache_ttl == "5m"


def test_opt_into_1_hour_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-1")
    monkeypatch.setenv("SIDEQUEST_ANTHROPIC_CACHE_TTL", "1h")
    client = AnthropicSdkClient()
    assert client.cache_ttl == "1h"


def test_invalid_cache_ttl_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-1")
    monkeypatch.setenv("SIDEQUEST_ANTHROPIC_CACHE_TTL", "banana")
    with pytest.raises(AnthropicSdkConfigError):
        AnthropicSdkClient()
