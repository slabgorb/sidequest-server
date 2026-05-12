#!/usr/bin/env python3
"""Latency-check script for story 48-2 (AC3).

Compares a single ``send_stateless`` call through ``build_llm_client()``
with ``SIDEQUEST_LLM_BACKEND=ollama`` against a recorded Claude baseline
and reports whether elapsed is within the 3x budget.

Operator runs this against a live Ollama instance during playtest. The
unit-test layer (``tests/agents/test_ollama_backend_e2e_48_2.py``) cannot
honestly assert the numeric budget because (a) there is no fixed Claude
baseline in the repo and (b) a CI-side latency assertion would be either
trivially fast (mocked) or unreliable (real network).

Usage::

    # Default prompt against a running Ollama instance, 3x budget vs a
    # ~7s narrator baseline:
    python scripts/ollama_latency_check.py --baseline-claude-s 7

    # Custom prompt + model hint:
    python scripts/ollama_latency_check.py \\
        --baseline-claude-s 7 \\
        --model sonnet \\
        --prompt "The party enters the cave."

Exit codes:
    0  elapsed within 3x budget — PASS
    1  elapsed exceeds 3x budget — FAIL
    2  Ollama unreachable / client error
    3  invocation error (bad args, env not set, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

DEFAULT_PROMPT = (
    "You are a SideQuest narrator. The party enters the cave. "
    "Describe what they see in one paragraph."
)
DEFAULT_SYSTEM = "You are a SideQuest narrator. Keep replies short for this latency check."
DEFAULT_MODEL = "sonnet"
BUDGET_MULTIPLIER = 3.0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ollama_latency_check",
        description=(
            "Latency check for story 48-2 (AC3): time a single send_stateless "
            "call through the Ollama backend and compare against a Claude "
            "baseline. Passes when Ollama latency <= 3x baseline."
        ),
        epilog=(
            "Exit codes: 0=PASS, 1=FAIL (budget exceeded), 2=Ollama "
            "unreachable, 3=invocation error."
        ),
    )
    parser.add_argument(
        "--baseline-claude-s",
        type=float,
        required=False,
        help=(
            "Claude baseline elapsed seconds for the same prompt. Required "
            "for pass/fail evaluation. Without it the script runs but only "
            "reports the elapsed time (no budget check; exits 0 unless "
            "Ollama is unreachable)."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model hint passed to send_stateless (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="User prompt to send (default: short cave-entry probe)",
    )
    parser.add_argument(
        "--system",
        default=DEFAULT_SYSTEM,
        help="System prompt (default: short SideQuest narrator framing)",
    )
    parser.add_argument(
        "--budget-multiplier",
        type=float,
        default=BUDGET_MULTIPLIER,
        help=(f"Budget multiplier vs Claude baseline (default: {BUDGET_MULTIPLIER}x per AC3)"),
    )
    return parser


async def _measure_one_call(model: str, system_prompt: str, user_prompt: str) -> float:
    """Run a single send_stateless and return elapsed seconds.

    Forces the Ollama backend by setting SIDEQUEST_LLM_BACKEND before the
    factory imports decide. Caller already does this; this function
    re-asserts it for defence-in-depth.
    """
    os.environ["SIDEQUEST_LLM_BACKEND"] = "ollama"
    # Late import so --help doesn't pay the import cost of the whole
    # sidequest package (also keeps this script invokable when the venv
    # has only a partial install).
    from sidequest.agents.llm_factory import build_llm_client

    client = build_llm_client()
    start = time.perf_counter()
    await client.send_stateless(
        system_prompt=system_prompt,
        user_message=user_prompt,
        model=model,
    )
    return time.perf_counter() - start


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        elapsed = asyncio.run(_measure_one_call(args.model, args.system, args.prompt))
    except Exception as exc:  # noqa: BLE001 — operator-facing tool, surface any failure verbatim
        # Don't dress this up with an exception taxonomy — the operator
        # needs the raw cause string to triage (Ollama down? model not
        # pulled? URL wrong?). Distinguish "unreachable" from "bad args"
        # via exit code for scripted callers.
        msg = str(exc)
        print(f"[ollama_latency_check] error: {msg}", file=sys.stderr)
        # Treat any send_stateless failure as "unreachable" for the
        # caller's purposes. Argparse handles its own usage errors via
        # SystemExit before we get here.
        return 2

    print(f"ollama elapsed: {elapsed:.3f}s")

    if args.baseline_claude_s is None:
        print(
            "[ollama_latency_check] no --baseline-claude-s provided; "
            "skipping budget check (informational run only)"
        )
        return 0

    budget = args.baseline_claude_s * args.budget_multiplier
    ratio = elapsed / args.baseline_claude_s if args.baseline_claude_s > 0 else float("inf")
    verdict = "PASS" if elapsed <= budget else "FAIL"
    print(
        f"claude baseline: {args.baseline_claude_s:.3f}s | "
        f"budget ({args.budget_multiplier:g}x): {budget:.3f}s | "
        f"ratio: {ratio:.2f}x | verdict: {verdict}"
    )
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
