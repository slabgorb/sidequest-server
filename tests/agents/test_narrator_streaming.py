"""Wiring tests for narrator streaming branch behind SIDEQUEST_NARRATOR_STREAMING."""

from __future__ import annotations


def test_narrator_module_exposes_streaming_capability_check():
    """The narrator module must expose a function that reports whether
    streaming is enabled via env var. This is the wiring test that ensures
    the flag is actually consulted and not orphaned."""
    from sidequest.agents.narrator import is_streaming_enabled

    assert callable(is_streaming_enabled)


def test_streaming_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SIDEQUEST_NARRATOR_STREAMING", raising=False)
    from sidequest.agents.narrator import is_streaming_enabled

    assert is_streaming_enabled() is False


def test_streaming_enabled_when_flag_is_one(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_NARRATOR_STREAMING", "1")
    from sidequest.agents.narrator import is_streaming_enabled

    assert is_streaming_enabled() is True


def test_streaming_disabled_when_flag_is_zero(monkeypatch):
    monkeypatch.setenv("SIDEQUEST_NARRATOR_STREAMING", "0")
    from sidequest.agents.narrator import is_streaming_enabled

    assert is_streaming_enabled() is False
