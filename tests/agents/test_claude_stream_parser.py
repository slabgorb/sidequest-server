"""Tests for NDJSON event parsing helpers."""

from __future__ import annotations

from sidequest.agents.claude_stream_parser import (
    extract_terminal_metadata,
    extract_text_delta,
    is_terminal_event,
)

# Sample event shapes — verified against `claude -p --output-format stream-json`
# during implementation. These are placeholders updated in Task 3 with
# captured fixture data.

DELTA_EVENT = {
    "type": "assistant",
    "message": {
        "content": [{"type": "text", "text": "Hello "}],
    },
}

TERMINAL_EVENT = {
    "type": "result",
    "result": "Hello world.",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
    "session_id": "sess-abc",
}

UNKNOWN_EVENT = {"type": "system", "subtype": "init"}


def test_extract_text_delta_returns_chunk_for_assistant_event():
    chunk = extract_text_delta(DELTA_EVENT)
    assert chunk == "Hello "


def test_extract_text_delta_returns_none_for_non_assistant():
    assert extract_text_delta(TERMINAL_EVENT) is None
    assert extract_text_delta(UNKNOWN_EVENT) is None


def test_is_terminal_event_recognizes_result_type():
    assert is_terminal_event(TERMINAL_EVENT) is True


def test_is_terminal_event_returns_false_for_non_terminal():
    assert is_terminal_event(DELTA_EVENT) is False
    assert is_terminal_event(UNKNOWN_EVENT) is False


def test_extract_terminal_metadata_pulls_usage_and_session():
    meta = extract_terminal_metadata(TERMINAL_EVENT)
    assert meta.full_text == "Hello world."
    assert meta.input_tokens == 100
    assert meta.output_tokens == 5
    assert meta.session_id == "sess-abc"


def test_extract_terminal_metadata_handles_missing_usage():
    minimal = {"type": "result", "result": "ok"}
    meta = extract_terminal_metadata(minimal)
    assert meta.full_text == "ok"
    assert meta.input_tokens is None
    assert meta.output_tokens is None
    assert meta.session_id is None
