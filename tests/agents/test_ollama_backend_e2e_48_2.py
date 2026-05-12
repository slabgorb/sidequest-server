"""End-to-end validation + audit tests for story 48-2.

Story 48-2: Validate SIDEQUEST_LLM_BACKEND=ollama end-to-end + audit
OllamaClient num_ctx pattern.

Coverage map (ACs from sprint/epic-48.yaml):
- AC1: one full playtest turn through the Ollama backend completes
- AC2: OTEL span confirms agent.backend="ollama"
- AC3: latency budget within 3x of Claude baseline (latency must be
       measurable; a comparison script must exist for manual playtest)
- AC4: OllamaClient num_ctx pattern reviewed; per-request num_ctx must
       not appear in any request body (per 48-1 finding: per-request
       num_ctx forces a KV cache reload every call). Audit outcome must
       be documented in the as-installed spec.

The narrator's canonical post-ADR-098 call path is `send_stateless`,
which for Ollama dispatches to `send_with_session(session_id=None, ...)`.
This module exercises all three surface methods so future narrator
refactors can't silently bypass the OTEL backend tag.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from sidequest.agents.claude_client import LlmClient
from sidequest.agents.llm_factory import (
    ENV_BACKEND,
    ENV_OLLAMA_URL,
    UnknownBackend,
    build_llm_client,
)
from sidequest.agents.ollama_client import (
    DEFAULT_OLLAMA_URL,
    OllamaClient,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    """Minimal urllib-style HTTP response usable as a context manager."""

    def __init__(self, body: bytes, status: int = 200, delay_s: float = 0.0) -> None:
        self._body = body
        self.status = status
        self._delay_s = delay_s

    def read(self) -> bytes:
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        return self._body

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _generate_body(text: str = "ok") -> bytes:
    return json.dumps(
        {
            "model": "sidequest-narrator:latest",
            "response": text,
            "done": True,
            "eval_count": 5,
            "prompt_eval_count": 7,
        }
    ).encode()


def _chat_body(text: str = "ok") -> bytes:
    return json.dumps(
        {
            "model": "sidequest-narrator:latest",
            "message": {"role": "assistant", "content": text},
            "done": True,
            "eval_count": 5,
            "prompt_eval_count": 7,
        }
    ).encode()


def _capture_http(
    responder: list[bytes],
    captured_bodies: list[dict[str, Any]] | None = None,
    captured_requests: list[Request] | None = None,
    delay_s: float = 0.0,
) -> Callable[[Request], _FakeHttpResponse]:
    """Build a fake http_fn that records request bodies and returns a
    queued response. `responder` is consumed FIFO so each call gets the
    next prepared payload. Pass `captured_bodies` to inspect JSON payloads,
    `captured_requests` to inspect URLs / headers."""

    def fake(req: Request) -> _FakeHttpResponse:
        if captured_requests is not None:
            captured_requests.append(req)
        if captured_bodies is not None:
            # urllib types req.data as a broad ReadableBuffer union; in
            # practice OllamaClient always supplies bytes (see
            # ollama_client.py:_post_generate / _post_chat).
            assert isinstance(req.data, bytes), (
                f"OllamaClient must serialize request bodies as bytes; got {type(req.data)}"
            )
            captured_bodies.append(json.loads(req.data))
        payload = responder.pop(0) if responder else _chat_body()
        return _FakeHttpResponse(payload, delay_s=delay_s)

    return fake


def _walk_for_key(obj: Any, key: str) -> bool:
    """Recursively scan a JSON-like dict/list structure for `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_walk_for_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_walk_for_key(v, key) for v in obj)
    return False


# ---------------------------------------------------------------------------
# AC1 — one full playtest turn through the Ollama backend completes
# ---------------------------------------------------------------------------


def test_ac1_factory_ollama_send_stateless_roundtrips_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrator's canonical post-ADR-098 path is send_stateless.
    With SIDEQUEST_LLM_BACKEND=ollama, the factory must yield a client
    that completes a send_stateless call against a (mocked) Ollama
    server, returns a ClaudeResponse tagged backend='ollama', AND
    actually sends the system+user content over the wire — the test
    captures the outbound `messages` array and asserts the merge contract
    in ollama_client.py:178-186 (send_stateless folds system_prompt into
    a single user-role message). Without this body check the test would
    pass even if user_message were silently dropped."""
    monkeypatch.setenv(ENV_BACKEND, "ollama")
    monkeypatch.setenv(ENV_OLLAMA_URL, "http://localhost:11434")

    client = build_llm_client()
    assert isinstance(client, OllamaClient)
    assert isinstance(client, LlmClient)

    captured_bodies: list[dict[str, Any]] = []
    # Inject the fake http function. The factory built OllamaClient with the
    # default http_fn, but we can swap the private slot for this test — it's
    # the documented dependency injection seam (see test_ollama_client.py).
    client._http = _capture_http(  # noqa: SLF001
        [_chat_body("narrator reply")],
        captured_bodies=captured_bodies,
    )

    system_text = "You are a SideQuest narrator."
    user_text = "The party enters the cave."
    response = asyncio.run(
        client.send_stateless(
            system_prompt=system_text,
            user_message=user_text,
            model="sonnet",
        )
    )

    assert response.text == "narrator reply", (
        "narrator reply text must propagate through the stateless wrapper"
    )
    assert response.backend == "ollama", (
        "ClaudeResponse.backend must be tagged 'ollama' so downstream code "
        "can distinguish provider; this is the wire format AC2 watches in OTEL."
    )
    assert response.input_tokens == 7
    assert response.output_tokens == 5

    # Verify the actual wire format. send_stateless merges system_prompt
    # and user_message into a single user-role entry — no separate system
    # message. Both texts must reach Ollama.
    assert len(captured_bodies) == 1, (
        f"send_stateless must result in exactly one HTTP call to /api/chat; "
        f"got {len(captured_bodies)} call(s)"
    )
    messages = captured_bodies[0]["messages"]
    assert isinstance(messages, list) and len(messages) == 1, (
        f"send_stateless must produce a single-entry messages array (system+user "
        f"merged into one user message per ollama_client.py:178-186); got: {messages!r}"
    )
    only_message = messages[0]
    assert only_message["role"] == "user", (
        f"merged stateless message must use role='user' (system text is folded "
        f"into the user prompt); got role={only_message['role']!r}"
    )
    assert system_text in only_message["content"], (
        f"system_prompt content must reach Ollama as part of the merged user "
        f"message; got content={only_message['content']!r}"
    )
    assert user_text in only_message["content"], (
        f"user_message content must reach Ollama; got content={only_message['content']!r}"
    )


def test_ac1_factory_ollama_send_with_session_supports_multi_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any future stateful caller uses send_with_session (the LocalDM
    preprocessor — historically the in-tree stateful consumer — is dormant
    per the 2026-04-28 spec). Two consecutive turns must produce a
    session_id on turn 1 and replay full history on turn 2."""
    monkeypatch.setenv(ENV_BACKEND, "ollama")

    client = build_llm_client()
    assert isinstance(client, OllamaClient)

    captured: list[dict[str, Any]] = []
    client._http = _capture_http(  # noqa: SLF001
        [_chat_body("turn 1 reply"), _chat_body("turn 2 reply")],
        captured_bodies=captured,
    )

    first = asyncio.run(
        client.send_with_session(
            prompt="turn 1",
            model="sonnet",
            session_id=None,
            system_prompt="sys",
        )
    )
    assert first.session_id is not None, (
        "send_with_session(session_id=None) must mint a fresh session id"
    )

    second = asyncio.run(
        client.send_with_session(
            prompt="turn 2",
            model="sonnet",
            session_id=first.session_id,
        )
    )
    assert second.session_id == first.session_id

    # Second request must replay history (system + u1 + a1 + u2).
    assert captured[1]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "turn 1 reply"},
        {"role": "user", "content": "turn 2"},
    ]


def test_ac1_factory_default_ollama_url_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SIDEQUEST_LLM_BACKEND=ollama but SIDEQUEST_OLLAMA_URL unset,
    HTTP requests must target DEFAULT_OLLAMA_URL — not a hardcoded duplicate.
    Asserts observable behavior (the URL actually sent over the wire),
    not internal storage form, so a refactor that normalises the URL at
    call time instead of construction time would not falsely break."""
    monkeypatch.setenv(ENV_BACKEND, "ollama")
    monkeypatch.delenv(ENV_OLLAMA_URL, raising=False)

    client = build_llm_client()
    assert isinstance(client, OllamaClient)

    captured_requests: list[Request] = []
    client._http = _capture_http(  # noqa: SLF001
        [_chat_body("ok")],
        captured_requests=captured_requests,
    )
    asyncio.run(
        client.send_stateless(system_prompt="sys", user_message="hi", model="sonnet")
    )

    assert len(captured_requests) == 1
    full_url = captured_requests[0].full_url
    assert full_url.startswith(DEFAULT_OLLAMA_URL.rstrip("/")), (
        f"factory must route HTTP calls to DEFAULT_OLLAMA_URL when env unset; "
        f"got full_url={full_url!r}"
    )


def test_ac1_factory_raises_unknown_backend_on_bad_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory's no-silent-fallback guarantee: an unrecognised
    SIDEQUEST_LLM_BACKEND value must raise UnknownBackend rather than
    falling through to a default. This pins the loud-failure contract
    in llm_factory.py:27-29 against future "helpful" defaulting."""
    monkeypatch.setenv(ENV_BACKEND, "groq")
    with pytest.raises(UnknownBackend) as exc_info:
        build_llm_client()
    # The error message must name the offending value so the operator can
    # debug the env without re-reading source.
    assert "groq" in str(exc_info.value), (
        f"UnknownBackend should name the rejected value; got {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# Wiring — AppFactory's default client_factory is build_llm_client
# ---------------------------------------------------------------------------


def test_wiring_create_app_default_client_factory_is_build_llm_client() -> None:
    """`create_app` must default `claude_client_factory` to
    `build_llm_client` so production deployments honour
    SIDEQUEST_LLM_BACKEND without manual factory injection.

    Behavioural wiring check: build the FastAPI app with no factory arg
    and verify the resolved default on `app.state` is *the same object*
    as `build_llm_client`. Per CLAUDE.md "Verify Wiring, Not Just
    Existence" — an `is` identity check on the resolved attribute beats
    any source-text grep because a refactor that satisfied the string
    while inlining a different callable would slip past, but cannot
    slip past `is`.
    """
    from sidequest.agents.llm_factory import build_llm_client as factory_under_test
    from sidequest.server.app import create_app

    app = create_app()
    resolved = app.state.claude_client_factory
    assert resolved is factory_under_test, (
        f"create_app() must store build_llm_client on app.state.claude_client_factory "
        f"when no factory arg is supplied; got {resolved!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — OTEL span confirms agent.backend="ollama"
# ---------------------------------------------------------------------------


def _find_span(exporter: Any, name: str) -> Any | None:
    """Return the first finished span matching `name` from the in-memory
    exporter. Returns None if none found.

    `exporter` is the OTEL `InMemorySpanExporter`; declared as `Any` to
    avoid importing the SDK type at module load (the fixture already
    pulls it). Return type is `ReadableSpan | None` for the same reason.
    """
    for span in exporter.get_finished_spans():
        if span.name == name:
            return span
    return None


def test_ac2_send_with_model_emits_agent_call_span_with_backend_ollama(
    otel_capture,
) -> None:
    """send_with_model must open an `agent.call` span tagged
    agent.backend='ollama'. This is the GM-panel "lie detector" attribute
    — without it the panel can't distinguish Ollama narration from Claude."""
    client = OllamaClient(http_fn=_capture_http([_generate_body("hi")]))
    asyncio.run(client.send_with_model("hello", model="sonnet"))

    span = _find_span(otel_capture, "agent.call")
    assert span is not None, "send_with_model must emit an `agent.call` span"
    assert span.attributes.get("agent.backend") == "ollama", (
        f"agent.call span must carry agent.backend='ollama'; got "
        f"{span.attributes.get('agent.backend')!r}"
    )


def test_ac2_send_with_session_emits_session_span_with_backend_ollama(
    otel_capture,
) -> None:
    """send_with_session must open an `agent.call.session` span tagged
    agent.backend='ollama'."""
    client = OllamaClient(http_fn=_capture_http([_chat_body("hi")]))
    asyncio.run(
        client.send_with_session(prompt="hi", model="sonnet", session_id=None, system_prompt="sys")
    )

    span = _find_span(otel_capture, "agent.call.session")
    assert span is not None, "send_with_session must emit an `agent.call.session` span"
    assert span.attributes.get("agent.backend") == "ollama"


def test_ac2_send_stateless_emits_session_span_with_backend_ollama(
    otel_capture,
) -> None:
    """send_stateless is the narrator's canonical post-ADR-098 path. Its
    span must carry agent.backend='ollama' since stateless dispatches to
    send_with_session under the hood."""
    client = OllamaClient(http_fn=_capture_http([_chat_body("hi")]))
    asyncio.run(
        client.send_stateless(
            system_prompt="sys",
            user_message="hi",
            model="sonnet",
        )
    )

    span = _find_span(otel_capture, "agent.call.session")
    assert span is not None
    assert span.attributes.get("agent.backend") == "ollama"


def test_ac2_factory_built_client_spans_tag_agent_backend_ollama(
    monkeypatch: pytest.MonkeyPatch,
    otel_capture,
) -> None:
    """End-to-end: env var → factory → call → OTEL backend tag. Catches
    regressions where the OTEL plumbing is bypassed in the factory path."""
    monkeypatch.setenv(ENV_BACKEND, "ollama")
    client = build_llm_client()
    assert isinstance(client, OllamaClient)
    client._http = _capture_http([_chat_body("ok")])  # noqa: SLF001

    asyncio.run(client.send_stateless(system_prompt="sys", user_message="hi", model="sonnet"))

    span = _find_span(otel_capture, "agent.call.session")
    assert span is not None
    assert span.attributes.get("agent.backend") == "ollama"


# ---------------------------------------------------------------------------
# AC3 — latency observable + comparison script exists
# ---------------------------------------------------------------------------


def test_ac3_span_records_request_duration_observable_via_otel(
    otel_capture,
) -> None:
    """OTEL spans must record observable elapsed time for Ollama calls
    so the GM panel and the latency-comparison script can compute the
    AC3 budget (≤3x Claude baseline) without bespoke instrumentation."""
    # 60ms simulated network round-trip — well above scheduler jitter
    # but cheap in CI.
    delay = 0.06
    client = OllamaClient(http_fn=_capture_http([_chat_body("hi")], delay_s=delay))
    asyncio.run(client.send_stateless(system_prompt="sys", user_message="hi", model="sonnet"))

    span = _find_span(otel_capture, "agent.call.session")
    assert span is not None
    duration_ns = span.end_time - span.start_time
    duration_s = duration_ns / 1_000_000_000
    # Floor of delay * 0.8 — tight enough to catch a span that closed before
    # the blocking call returned (an instrumentation-placement bug), still
    # tolerant of scheduler jitter. A loose floor (e.g. delay * 0.5) would
    # pass with quarter-elapsed timing, defeating the regression-guard intent.
    assert duration_s >= delay * 0.8, (
        f"span duration {duration_s:.3f}s must reflect the simulated "
        f"network delay of {delay:.3f}s within scheduler jitter (floor: "
        f"{delay * 0.8:.3f}s); span may have closed before the blocking "
        f"call completed."
    )
    # Sanity ceiling: not absurdly long
    assert duration_s < 5.0, (
        f"span duration {duration_s:.3f}s is implausibly large — span timing may be broken"
    )


def test_ac3_latency_comparison_script_exists_and_is_invocable() -> None:
    """AC3 requires latency to be within 3x of the Claude baseline. The
    only honest way to assert this is a runtime comparison against a real
    Ollama instance — i.e., a script the operator runs during playtest.

    This test pins that the comparison script EXISTS at a known location
    and is invocable (responds to --help with exit 0). Tests intentionally
    do NOT assert latency numbers; the playtest evidence is captured in
    the session file / archive.
    """
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "sidequest-server" / "scripts" / "ollama_latency_check.py",
        repo_root / "scripts" / "ollama_latency_check.py",
    ]
    found = [p for p in candidates if p.is_file()]
    assert found, (
        "AC3 latency-check script must exist at one of: "
        f"{[str(p) for p in candidates]}. The script must compare an "
        "Ollama-backend call against a recorded Claude baseline and "
        "report whether elapsed is within the 3x budget."
    )
    script = found[0]
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{script} --help exited {result.returncode}; stderr: {result.stderr!r}"
    )
    assert "latency" in result.stdout.lower() or "latency" in result.stderr.lower(), (
        f"{script} --help should describe the latency comparison; got: {result.stdout!r}"
    )


def _load_latency_script() -> Any:
    """Import `ollama_latency_check.py` as a module by file path so we
    can call its `main()` function in-process. The script lives in
    `sidequest-server/scripts/`, which is not a Python package — using
    importlib.util is the standard way to load a single .py file."""
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "sidequest-server" / "scripts" / "ollama_latency_check.py"
    spec = importlib.util.spec_from_file_location(
        "_test_ollama_latency_check", script_path
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load script spec for {script_path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ac3_script_imports_build_llm_client_at_module_top_level() -> None:
    """Per CLAUDE.md "No Silent Fallbacks": a missing or broken
    `sidequest` package install must surface AT SCRIPT LOAD, not be
    deferred past --help. The `build_llm_client` import must live at
    module top level — never inside a function body — so a partial venv
    cannot pass --help and only fail later inside `_measure_one_call`.

    Static check: parse the script with ast and verify the
    `from sidequest.agents.llm_factory import ...` ImportFrom node is a
    direct child of the Module body, not nested inside a FunctionDef or
    AsyncFunctionDef.
    """
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "sidequest-server" / "scripts" / "ollama_latency_check.py"
    src = script_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    sidequest_imports = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and n.module is not None
        and n.module.startswith("sidequest.")
    ]
    assert sidequest_imports, (
        f"latency-check script must import from sidequest.* at least once; "
        f"none found in {script_path}"
    )

    module_level = [n for n in sidequest_imports if any(stmt is n for stmt in tree.body)]
    assert module_level, (
        "latency-check script must import sidequest.* AT MODULE TOP LEVEL, not "
        "inside a function body. Deferred imports mask ModuleNotFoundError past "
        "--help, violating CLAUDE.md's No Silent Fallbacks rule. Move "
        "`from sidequest.agents.llm_factory import build_llm_client` out of "
        f"_measure_one_call() to the top-level imports block. File: {script_path}"
    )


def test_ac3_script_rejects_zero_or_negative_baseline_claude_s() -> None:
    """A negative or zero `--baseline-claude-s` produces nonsensical
    output (negative budget → guaranteed FAIL regardless of elapsed;
    zero is short-circuited to float('inf') and silently produces a
    bogus ratio). The script must validate the argument and exit loudly
    via `argparse.error` (SystemExit) before reaching `send_stateless`.

    This test exercises `main()` in-process with `pytest.raises` —
    catching the argparse SystemExit. After Dev's fix, main() raises
    SystemExit immediately on argparse error; before the fix, main()
    proceeds to asyncio.run and returns 1 or 2 depending on Ollama
    reachability (no exception raised), so the test fails.
    """
    module = _load_latency_script()

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--baseline-claude-s", "-1.5"])
    # argparse.error() conventionally exits with code 2.
    assert exc_info.value.code != 0, (
        f"argparse.error on negative baseline must exit non-zero; got "
        f"{exc_info.value.code}"
    )

    # Same guard for zero.
    with pytest.raises(SystemExit):
        module.main(["--baseline-claude-s", "0"])


# ---------------------------------------------------------------------------
# AC4 — OllamaClient num_ctx audit (regression guards)
# ---------------------------------------------------------------------------


def test_ac4_send_with_model_request_body_has_no_num_ctx_anywhere() -> None:
    """48-1 found per-request `num_ctx` forces a KV cache reload (~28s
    per call). OllamaClient must NEVER send num_ctx in the request body
    of /api/generate. Pin this as a regression guard."""
    captured: list[dict[str, Any]] = []
    client = OllamaClient(http_fn=_capture_http([_generate_body("ok")], captured_bodies=captured))
    asyncio.run(client.send_with_model("hi", model="sonnet"))

    assert len(captured) == 1
    body = captured[0]
    assert not _walk_for_key(body, "num_ctx"), (
        f"/api/generate body must NOT contain a `num_ctx` key anywhere; "
        f"per-request num_ctx triggers a model reload on every call "
        f"(48-1 finding, ~28s cost). Body sent: {body!r}"
    )


def test_ac4_send_with_session_request_body_has_no_num_ctx_anywhere() -> None:
    """Same regression guard for /api/chat."""
    captured: list[dict[str, Any]] = []
    client = OllamaClient(http_fn=_capture_http([_chat_body("ok")], captured_bodies=captured))
    asyncio.run(
        client.send_with_session(prompt="hi", model="sonnet", session_id=None, system_prompt="sys")
    )

    assert len(captured) == 1
    body = captured[0]
    assert not _walk_for_key(body, "num_ctx"), (
        f"/api/chat body must NOT contain a `num_ctx` key anywhere. Body sent: {body!r}"
    )


def test_ac4_send_stateless_request_body_has_no_num_ctx_anywhere() -> None:
    """send_stateless is the narrator's hot path — guard it directly even
    though it currently delegates to send_with_session."""
    captured: list[dict[str, Any]] = []
    client = OllamaClient(http_fn=_capture_http([_chat_body("ok")], captured_bodies=captured))
    asyncio.run(client.send_stateless(system_prompt="sys", user_message="hi", model="sonnet"))

    assert len(captured) == 1
    body = captured[0]
    assert not _walk_for_key(body, "num_ctx"), (
        f"send_stateless must not introduce num_ctx in its delegate body. Body sent: {body!r}"
    )


def test_ac4_ollama_client_source_has_no_num_ctx_reference() -> None:
    """Static guard: the OllamaClient source file must not reference
    `num_ctx` anywhere. If a future maintainer reintroduces it (perhaps
    copying from older Ollama docs), this test catches it before the
    KV-cache-reload regression ships. Comments explaining WHY it's
    absent should reference this test by name, not the bare token."""
    from sidequest.agents import ollama_client as ollama_client_module

    src = Path(ollama_client_module.__file__).read_text(encoding="utf-8")
    assert "num_ctx" not in src, (
        "OllamaClient source must not reference `num_ctx`. Per the 48-1 "
        "audit, num_ctx must be configured at load time via the Ollama "
        "Modelfile (PARAMETER num_ctx ...), never sent per-request. "
        "If you need to document the absence, do so via a comment that "
        "names this test (e.g. 'see test_ac4_ollama_client_source_has_no_num_ctx_reference')."
    )


def test_ac4_audit_outcome_documented_in_as_installed_spec() -> None:
    """AC4 requires the audit's outcome to be written down. Per the
    project's no-silent-fallbacks ethos and CLAUDE.md's OTEL principle,
    a one-off code review without a written conclusion isn't a real
    audit — future maintainers can't verify it happened.

    The natural location is the existing as-installed doc, which already
    flags this follow-up at line 60. The doc must be updated to record
    the AC4 conclusion for story 48-2.
    """
    repo_root = Path(__file__).resolve().parents[3]
    spec = (
        repo_root
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-05-06-local-qwen-code-editor-as-installed.md"
    )
    assert spec.is_file(), f"as-installed spec missing at {spec}"
    text = spec.read_text(encoding="utf-8")
    # Require a marker that ties the audit to story 48-2 and the
    # OllamaClient subject.
    assert "48-2" in text, (
        f"as-installed spec must reference story 48-2 to record the AC4 audit outcome at {spec}"
    )
    assert "OllamaClient" in text, (
        "as-installed spec must explicitly name OllamaClient in the audit conclusion"
    )
    # Require a written conclusion. Accept either outcome wording — the
    # important thing is that SOMETHING was concluded in prose.
    conclusion_markers = [
        "no per-request",
        "no num_ctx",
        "no num-ctx",
        "audit conclusion",
        "audit outcome",
        "audit complete",
    ]
    found_markers = [m for m in conclusion_markers if m.lower() in text.lower()]
    assert found_markers, (
        f"as-installed spec must contain an explicit AC4 audit conclusion. "
        f"Looked for any of: {conclusion_markers}. Spec at {spec} has none."
    )


def test_ac4_audit_outcome_notes_send_stateless_system_user_merge() -> None:
    """The audit conclusion describes the `/api/chat` body shape for
    `send_stateless`. The shape is technically `{"model", "messages", "stream"}`,
    but the `messages` array is NOT the standard `[system, user]` two-role
    structure — `send_stateless` (ollama_client.py:178-186) merges
    system_prompt into a single combined string and passes it as one
    user-role entry with `system_prompt=None`. A reader who skims the
    audit and assumes a normal `[system, user]` shape will be surprised
    by Ollama-specific behavior (some models weight system messages
    differently from user content).

    The audit doc must acknowledge this merge — otherwise the prose
    over-claims fidelity to standard chat semantics.
    """
    repo_root = Path(__file__).resolve().parents[3]
    spec = (
        repo_root
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-05-06-local-qwen-code-editor-as-installed.md"
    )
    text = spec.read_text(encoding="utf-8")
    # Look for either: an explicit "merge" / "merged" / "merges" reference
    # in the AC4 section, or a parenthetical naming "single combined" /
    # "single user" / "user-role entry" / "one user". Any of these phrases
    # signals the merge has been documented.
    merge_markers = [
        "merges system_prompt",
        "merge system_prompt",
        "merged into",
        "single user",
        "one user-role",
        "single combined",
        "user-role entry",
    ]
    found = [m for m in merge_markers if m.lower() in text.lower()]
    assert found, (
        f"as-installed spec must note that send_stateless merges system_prompt "
        f"into a single user-role message (not a standard [system, user] pair). "
        f"Looked for any of: {merge_markers}. Spec at {spec} has none. "
        f"Suggested addition (parenthetical for the /api/chat row): "
        f"'(note: send_stateless merges system_prompt into the user message as a "
        f"single combined string before calling send_with_session; the messages "
        f"array therefore contains one user-role entry, not a system+user pair).'"
    )
