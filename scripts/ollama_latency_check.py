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

Exit codes from main():
    0  elapsed within 3x budget — PASS (or informational run without --baseline-claude-s)
    1  elapsed exceeds 3x budget — FAIL
    2  Ollama transport failure during send_stateless (network unreachable,
       HTTP error, malformed response — anything that surfaces as
       OllamaClientError or a lower-level transport exception)
    3  configuration error — unknown model hint, unknown backend, or any
       other client-side misconfiguration that does not indicate Ollama
       itself is down

Note: argparse handles bad CLI arguments via its own SystemExit (exit
code 2 from argparse itself); main() does not return that code. The
overlap between argparse's exit-2 and main()'s exit-2 is unfortunate
but unavoidable without subclassing ArgumentParser — operators
distinguish by reading stderr (argparse prints a "usage: …" prefix;
main() prints "[ollama_latency_check] error: …").
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Top-level import so a missing or broken `sidequest` package install
# fails AT SCRIPT LOAD rather than silently passing --help and only
# surfacing ModuleNotFoundError when the operator actually runs a
# measurement (CLAUDE.md: No Silent Fallbacks).
from sidequest.agents.llm_factory import UnknownBackend, build_llm_client
from sidequest.agents.ollama_client import OllamaClientError, UnknownModel

DEFAULT_PROMPT = (
    "You are a SideQuest narrator. The party enters the cave. "
    "Describe what they see in one paragraph."
)
DEFAULT_SYSTEM = "You are a SideQuest narrator. Keep replies short for this latency check."
DEFAULT_MODEL = "sonnet"
BUDGET_MULTIPLIER = 3.0

EXIT_PASS = 0
EXIT_BUDGET_EXCEEDED = 1
EXIT_OLLAMA_TRANSPORT = 2
EXIT_CONFIG_ERROR = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ollama_latency_check",
        description=(
            "Latency check for story 48-2 (AC3): time a single send_stateless "
            "call through the Ollama backend and compare against a Claude "
            "baseline. Passes when Ollama latency <= 3x baseline."
        ),
        epilog=(
            "Exit codes from main(): 0=PASS (or no baseline supplied), "
            "1=FAIL (budget exceeded), 2=Ollama transport failure, "
            "3=configuration error (unknown model / backend)."
        ),
    )
    parser.add_argument(
        "--baseline-claude-s",
        type=float,
        required=False,
        help=(
            "Claude baseline elapsed seconds for the same prompt. Required "
            "for pass/fail evaluation; must be > 0. Without it the script "
            "runs but only reports the elapsed time (no budget check)."
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
    """Run a single send_stateless against the Ollama backend and return
    elapsed seconds.

    Sets ``SIDEQUEST_LLM_BACKEND=ollama`` unconditionally before invoking
    the factory — this is the sole assignment, not a defence-in-depth
    re-assert. The caller (main()) does not pre-set the env var.
    """
    os.environ["SIDEQUEST_LLM_BACKEND"] = "ollama"

    client = build_llm_client()
    start = time.perf_counter()
    await client.send_stateless(
        system_prompt=system_prompt,
        user_message=user_prompt,
        model=model,
    )
    return time.perf_counter() - start


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Reject non-positive baselines loudly via argparse.error (SystemExit
    # with exit code 2). A negative baseline produces a negative budget
    # ceiling, guaranteeing a misleading FAIL verdict regardless of
    # elapsed; zero would short-circuit the ratio guard to float('inf').
    # Either case is operator-facing nonsense, not a usable measurement.
    if args.baseline_claude_s is not None and args.baseline_claude_s <= 0:
        parser.error(f"--baseline-claude-s must be > 0 (got: {args.baseline_claude_s})")

    try:
        elapsed = asyncio.run(_measure_one_call(args.model, args.system, args.prompt))
    except (UnknownModel, UnknownBackend) as exc:
        # Client-side configuration error: bad --model hint, bad
        # SIDEQUEST_LLM_BACKEND env var. Ollama itself may be fine.
        print(f"[ollama_latency_check] config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except OllamaClientError as exc:
        # Ollama transport / response failure: server down, HTTP non-200,
        # non-JSON body, session-id unknown after restart.
        print(f"[ollama_latency_check] ollama error: {exc}", file=sys.stderr)
        return EXIT_OLLAMA_TRANSPORT
    except Exception as exc:  # noqa: BLE001 — operator-facing tool: surface unexpected failures verbatim
        # Anything outside the taxonomies above (urllib OS error, asyncio
        # TimeoutError, etc.) bubbles up as a transport failure so the
        # operator at least sees the raw cause on stderr.
        print(f"[ollama_latency_check] unexpected error: {exc}", file=sys.stderr)
        return EXIT_OLLAMA_TRANSPORT

    print(f"ollama elapsed: {elapsed:.3f}s")

    if args.baseline_claude_s is None:
        print(
            "[ollama_latency_check] no --baseline-claude-s provided; "
            "skipping budget check (informational run only)"
        )
        return EXIT_PASS

    budget = args.baseline_claude_s * args.budget_multiplier
    # baseline > 0 enforced above, so the ratio is always well-defined.
    ratio = elapsed / args.baseline_claude_s
    verdict = "PASS" if elapsed <= budget else "FAIL"
    print(
        f"claude baseline: {args.baseline_claude_s:.3f}s | "
        f"budget ({args.budget_multiplier:g}x): {budget:.3f}s | "
        f"ratio: {ratio:.2f}x | verdict: {verdict}"
    )
    return EXIT_PASS if verdict == "PASS" else EXIT_BUDGET_EXCEEDED


if __name__ == "__main__":
    sys.exit(main())
