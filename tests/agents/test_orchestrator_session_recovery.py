"""ADR-066 §8 reactive narrator session crash recovery — orchestrator tests.

Covers the four recovery error classes (context-overflow, session-not-found,
transient, unknown), the §9 warm-reboot frame splicing into the Full-tier
prompt builder, and the §10 ``narrator.session_rotated`` /
``narrator.unrecoverable`` OTEL span emission.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from sidequest.agents.claude_client import ClaudeClient
from sidequest.agents.orchestrator import (
    NarratorPromptTier,
    Orchestrator,
    TurnContext,
)

# ---------------------------------------------------------------------------
# Fakes — process doubles that can simulate CLI failure modes
# ---------------------------------------------------------------------------


class FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in that supports stderr."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:  # pragma: no cover - never reached on FakeProcess
        pass

    async def wait(self) -> int:
        return self.returncode


def _ok_envelope(text: str, session_id: str) -> bytes:
    return json.dumps(
        {
            "result": text,
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    ).encode()


_HAPPY_NARRATION = (
    "**The Throat**\n\nYou step deeper into the cavern.\n\n```game_patch\n{}\n```"
)


def _make_scripted_spawn(
    script: list[Any],
    success_session_id: str = "session-rebuild-001",
) -> Callable[..., Any]:
    """Build a spawn_fn whose return values follow ``script`` on each call.

    Each entry can be:

    * ``"ok"`` — return a happy FakeProcess with a successful narration
    * ``("fail", "<stderr signature>")`` — return a FakeProcess with non-zero
      returncode and the given stderr text (drives ``SubprocessFailed``)
    * ``("raise", <Exception instance>)`` — raise the exception (simulates
      transient I/O / spawn failures)

    Calls beyond the script length raise ``AssertionError`` so unintended
    extra invocations are visible.
    """
    state = {"i": 0}

    async def spawn_fn(command: str, *args: str, env: Any = None, **kwargs: Any):
        i = state["i"]
        state["i"] = i + 1
        assert i < len(script), (
            f"spawn_fn called more times than scripted: call #{i + 1}, "
            f"only {len(script)} entries"
        )
        step = script[i]
        if step == "ok":
            return FakeProcess(stdout=_ok_envelope(_HAPPY_NARRATION, success_session_id))
        if isinstance(step, tuple) and step[0] == "fail":
            return FakeProcess(stdout=b"", stderr=step[1].encode(), returncode=1)
        if isinstance(step, tuple) and step[0] == "raise":
            raise step[1]
        raise AssertionError(f"Unknown spawn script step: {step!r}")

    spawn_fn.calls = state  # type: ignore[attr-defined]
    return spawn_fn


def _orchestrator_with(
    spawn_fn: Callable[..., Any],
    *,
    recap: str | None = "## Previously On…\n\n- You crossed the bridge.\n",
) -> Orchestrator:
    """Build an Orchestrator wired with a scripted ClaudeClient and a recap stub.

    The recap stub stands in for ``SessionStore.generate_recap()``; Dev
    plumbs the real callable in production but the orchestrator only needs
    a ``Callable[[], str | None]`` to compose the warm-reboot frame.
    """
    client = ClaudeClient(spawn_fn=spawn_fn)
    recap_provider = (lambda: recap) if recap is not None else (lambda: None)
    return Orchestrator(client=client, recap_provider=recap_provider)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AC 1 — Context-overflow recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_overflow_resets_session_and_retries():
    """ADR-066 §8 — context_window_full triggers reset + retry, no exception."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full: exceeded 1000000 tokens"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired-session-xyz")

    context = TurnContext(character_name="Kael", current_location="The Throat")
    result = await orch.run_narration_turn("look around", context)

    assert not result.is_degraded, (
        "Context-overflow must recover transparently; player should not see a stall."
    )
    assert "deeper into the cavern" in result.narration
    # Recovery must reset the dead session — the new session_id from the
    # successful retry is what's stored.
    assert orch._narrator_session_id == "session-rebuild-001"


@pytest.mark.asyncio
async def test_context_overflow_emits_session_rotated_span(otel_capture):
    """ADR-066 §10 — recovery emits narrator.session_rotated with reason=cli_error."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired-session-xyz")

    context = TurnContext(character_name="Kael")
    await orch.run_narration_turn("look around", context)

    spans = otel_capture.get_finished_spans()
    rotated = [s for s in spans if s.name == "narrator.session_rotated"]
    assert len(rotated) == 1, (
        f"Expected exactly one narrator.session_rotated span; "
        f"saw {[s.name for s in spans]}"
    )
    attrs = rotated[0].attributes or {}
    assert attrs.get("reason") == "cli_error"
    assert attrs.get("cli_error_signature") == "context_window_full"
    assert attrs.get("recap_chars") is not None
    assert attrs.get("rebuild_latency_ms") is not None


@pytest.mark.asyncio
async def test_context_overflow_recovery_uses_full_tier_not_delta():
    """The recovery turn must rebuild the full prompt, not send a Delta."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired-session-xyz")
    # Establish session genre so a non-recovery turn would normally pick Delta.
    orch._session_genre = "caverns_and_claudes"

    context = TurnContext(character_name="Kael", genre="caverns_and_claudes")
    result = await orch.run_narration_turn("look around", context)

    assert result.prompt_tier == NarratorPromptTier.Full, (
        "After session reset the rebuild turn must use Full tier so the "
        "static prompt context is re-established in the new session."
    )


# ---------------------------------------------------------------------------
# AC 2 — Session-not-found recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_not_found_resets_and_retries():
    """ADR-066 §8 — session_not_found from CLI also routes to recovery."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "session_not_found: session-id 'expired-xyz' not found"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired-xyz")

    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("look around", context)

    assert not result.is_degraded
    assert orch._narrator_session_id == "session-rebuild-001"


@pytest.mark.asyncio
async def test_session_not_found_emits_session_rotated_with_reason(otel_capture):
    """ADR-066 §10 — session_not_found emits span with reason=session_expired."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "session_not_found"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired-xyz")

    await orch.run_narration_turn("look around", TurnContext(character_name="Kael"))

    rotated = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "narrator.session_rotated"
    ]
    assert len(rotated) == 1
    attrs = rotated[0].attributes or {}
    assert attrs.get("reason") == "session_expired"


# ---------------------------------------------------------------------------
# AC 4 — Transient/network error retries once before rotating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_error_retries_once_on_same_session():
    """ADR-066 §8 — transient I/O retries on the SAME session, no rotation."""
    spawn = _make_scripted_spawn(
        [
            ("raise", OSError("connection reset by peer")),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("session-warm")

    context = TurnContext(character_name="Kael")
    result = await orch.run_narration_turn("look around", context)

    assert not result.is_degraded
    # The original session must be preserved — the second call's fresh
    # session_id is used because the canned spawn always returns it,
    # but the orchestrator must NOT have called reset_narrator_session()
    # on the way to retrying. Verified by checking that no rotation span
    # fired (see companion test).


@pytest.mark.asyncio
async def test_transient_error_retry_does_not_emit_rotation_span(otel_capture):
    spawn = _make_scripted_spawn(
        [
            ("raise", OSError("connection reset by peer")),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("session-warm")

    await orch.run_narration_turn("look around", TurnContext(character_name="Kael"))

    rotated = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "narrator.session_rotated"
    ]
    assert rotated == [], (
        "A transient retry must NOT trigger session rotation; only persistent "
        "failures escalate to rotation per ADR-066 §8."
    )


@pytest.mark.asyncio
async def test_transient_error_after_retry_falls_back_to_rotation():
    """If the retry on the same session also fails, escalate to rotation."""
    spawn = _make_scripted_spawn(
        [
            ("raise", OSError("connection reset")),
            ("raise", OSError("connection reset again")),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("session-warm")

    result = await orch.run_narration_turn(
        "look around", TurnContext(character_name="Kael")
    )

    assert not result.is_degraded
    assert orch._narrator_session_id == "session-rebuild-001"


# ---------------------------------------------------------------------------
# AC 3 — Unknown error: recovery, then graceful stall on double failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_cli_error_resets_and_retries():
    """ADR-066 §8 — catch-all unknown error still routes to recovery first."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "some_brand_new_error_signature_we_havent_seen"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("session-warm")

    result = await orch.run_narration_turn(
        "look around", TurnContext(character_name="Kael", current_location="The Throat")
    )

    assert not result.is_degraded


@pytest.mark.asyncio
async def test_double_failure_emits_unrecoverable_and_stalls(otel_capture):
    """ADR-066 §8 — if recovery itself fails, emit narrator.unrecoverable + stall."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full"),
            ("fail", "context_window_full_again_during_rebuild"),
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("session-warm")

    result = await orch.run_narration_turn(
        "look around",
        TurnContext(character_name="Kael", current_location="The Throat"),
    )

    # Player gets a graceful in-fiction stall, NOT a server crash.
    assert result.is_degraded
    assert "The Throat" in result.narration
    assert "holds its breath" in result.narration

    span_names = [s.name for s in otel_capture.get_finished_spans()]
    assert "narrator.unrecoverable" in span_names, (
        f"Double failure must emit narrator.unrecoverable; saw {span_names}"
    )


# ---------------------------------------------------------------------------
# AC 6 — Warm-reboot frame splicing (ADR-066 §9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_tier_prompt_accepts_rebuild_header():
    """build_narrator_prompt must accept an optional rebuild_header argument."""
    orch = _orchestrator_with(_make_scripted_spawn(["ok"]))
    context = TurnContext(character_name="Kael")

    header = (
        "[SESSION CONTINUATION]\n\n"
        "You do not have verbatim memory of prior turns.\n\n"
        "[PREVIOUSLY ON]\n## Previously On…\n\n- You crossed the bridge.\n"
    )
    prompt, _ = await orch.build_narrator_prompt(
        "look around",
        context,
        tier=NarratorPromptTier.Full,
        rebuild_header=header,  # type: ignore[call-arg]
    )

    assert "[SESSION CONTINUATION]" in prompt
    assert "[PREVIOUSLY ON]" in prompt
    assert "You crossed the bridge" in prompt


@pytest.mark.asyncio
async def test_rebuild_header_ordering_precedes_player_action():
    """The rebuild header must appear before the player's action in the prompt."""
    orch = _orchestrator_with(_make_scripted_spawn(["ok"]))
    context = TurnContext(character_name="Kael")

    header = "[SESSION CONTINUATION]\n\n[PREVIOUSLY ON]\n- prior beat\n"
    prompt, _ = await orch.build_narrator_prompt(
        "cast spell",
        context,
        tier=NarratorPromptTier.Full,
        rebuild_header=header,  # type: ignore[call-arg]
    )

    continuation_pos = prompt.find("[SESSION CONTINUATION]")
    action_pos = prompt.find("cast spell")
    assert continuation_pos != -1, "rebuild header missing from prompt"
    assert action_pos != -1
    assert continuation_pos < action_pos, (
        "Warm-reboot frame must come BEFORE the player action so the model "
        "sees 'this is a continuation' before responding."
    )


@pytest.mark.asyncio
async def test_recovery_path_splices_recap_into_rebuild_prompt():
    """End-to-end: when recovery fires, the rebuild prompt carries the recap."""
    captured_prompts: list[str] = []
    state = {"i": 0}

    async def capturing_spawn(command: str, *args: str, env: Any = None, **kwargs: Any):
        # Capture the prompt text from -p (last positional arg before --output-format).
        try:
            p_idx = list(args).index("-p")
            captured_prompts.append(args[p_idx + 1])
        except (ValueError, IndexError):  # pragma: no cover - test sanity
            pass

        i = state["i"]
        state["i"] = i + 1
        if i == 0:
            return FakeProcess(stderr=b"context_window_full", returncode=1)
        return FakeProcess(stdout=_ok_envelope(_HAPPY_NARRATION, "session-rebuild-001"))

    client = ClaudeClient(spawn_fn=capturing_spawn)
    orch = Orchestrator(
        client=client,
        recap_provider=lambda: "## Previously On…\n\n- You crossed the bridge.\n",  # type: ignore[call-arg]
    )
    orch.set_narrator_session_id("expired")

    await orch.run_narration_turn("look around", TurnContext(character_name="Kael"))

    assert len(captured_prompts) >= 2, (
        "Expected at least two CLI invocations: failed --resume + rebuild"
    )
    rebuild_prompt = captured_prompts[-1]
    assert "[SESSION CONTINUATION]" in rebuild_prompt
    assert "[PREVIOUSLY ON]" in rebuild_prompt
    assert "You crossed the bridge" in rebuild_prompt


@pytest.mark.asyncio
async def test_recovery_handles_empty_recap_gracefully():
    """If generate_recap() returns None (empty narrative log), recovery still works."""
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn, recap=None)
    orch.set_narrator_session_id("expired")

    result = await orch.run_narration_turn(
        "look around", TurnContext(character_name="Kael")
    )

    assert not result.is_degraded


# ---------------------------------------------------------------------------
# AC 5 (cont.) — span attribute coverage across reasons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stderr_signature, expected_reason",
    [
        ("context_window_full", "cli_error"),
        ("maximum_tokens_exceeded", "cli_error"),
        ("session_not_found", "session_expired"),
        ("session_expired", "session_expired"),
    ],
)
async def test_session_rotated_span_reason_classification(
    otel_capture, stderr_signature: str, expected_reason: str
):
    """Span ``reason`` must reflect the classified error class per ADR-066 §8/§10."""
    spawn = _make_scripted_spawn([("fail", stderr_signature), "ok"])
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired")

    await orch.run_narration_turn("look around", TurnContext(character_name="Kael"))

    rotated = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == "narrator.session_rotated"
    ]
    assert len(rotated) == 1
    assert (rotated[0].attributes or {}).get("reason") == expected_reason


# ---------------------------------------------------------------------------
# AC 7 — Wiring guard: recovery is reachable from the production turn pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_handler_is_wired_into_run_narration_turn():
    """Wiring guard — the recovery code path must be called from
    ``Orchestrator.run_narration_turn``, not just exist as a private helper.

    We prove this by simulating a CLI failure: if recovery were not wired in,
    the result would be a degraded stall (current behavior) instead of a
    successful narration from the rebuild. CLAUDE.md requires every test
    suite to include at least one wiring test.
    """
    spawn = _make_scripted_spawn(
        [
            ("fail", "context_window_full"),
            "ok",
        ]
    )
    orch = _orchestrator_with(spawn)
    orch.set_narrator_session_id("expired")

    result = await orch.run_narration_turn(
        "look around", TurnContext(character_name="Kael", current_location="The Throat")
    )

    assert not result.is_degraded, (
        "WIRING REGRESSION: orchestrator returned a degraded stall instead of "
        "recovering. Either the error handler is not wrapped around the CLI "
        "call, or it does not retry after rotation. Without recovery wired "
        "into run_narration_turn, the playtest crash class is not fixed."
    )
    assert "deeper into the cavern" in result.narration
