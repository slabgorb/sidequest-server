#!/usr/bin/env python3
"""A/B evaluation harness CLI for story 48-4 (epic 48, Group F).

Runs identical prompts through a Claude-family backend and the local Ollama
backend, then writes a markdown diff report. The unit layer
(``tests/agents/test_ab_eval_harness.py``) is fully mocked; this script is the
*operator* layer — the live A/B must be run on Keith's M3 Ultra, the only host
where Ollama serves the local model. Mirrors story 48-2's
``ollama_latency_check.py`` operator-evidence pattern.

Usage::

    # Single prompt (diagnostic):
    python scripts/ab_eval_harness_cli.py \\
        --user-prompt "The party enters the cave." \\
        --output-md /tmp/ab_eval.md

    # Batch over a mined corpus slice:
    python scripts/ab_eval_harness_cli.py \\
        --input-jsonl ~/.sidequest/corpus/mined/caverns_and_claudes.jsonl \\
        --genre caverns_and_claudes --sample-size 10 \\
        --output-md /tmp/cc_ab_eval.md

Exit codes:
    0  success
    1  Claude backend error
    3  configuration error (bad args, missing/invalid JSONL, non-LlmClient)
    4  Ollama unreachable — graceful no-op; the live A/B is operator-evidence
       only and must be captured on the M3 Ultra (report records the note).
       Any OllamaClientError (down, transport, malformed) routes here: on the
       operator path "Ollama raised" and "Ollama absent" are one signal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import pydantic

# Top-level imports so a broken `sidequest` install fails AT SCRIPT LOAD, not
# at --help (CLAUDE.md: No Silent Fallbacks). AC5 mirrors ollama_latency_check.
from sidequest.agents.ab_eval_harness import AbEvalHarness
from sidequest.agents.claude_client import ClaudeClient, LlmClient, LlmClientError
from sidequest.agents.llm_factory import (
    ENV_BACKEND,
    UnknownBackend,
    build_llm_client,
)
from sidequest.agents.ollama_client import OllamaClientError
from sidequest.corpus.schema import TrainingPair

EXIT_PASS = 0
EXIT_CLAUDE_ERROR = 1
# Exit code 2 is intentionally unused: all OllamaClientError routes to
# EXIT_OLLAMA_UNREACHABLE (operator-evidence no-op, AC4). See module docstring.
EXIT_CONFIG_ERROR = 3
EXIT_OLLAMA_UNREACHABLE = 4

DEFAULT_SYSTEM = "You are a SideQuest narrator."

OPERATOR_NOTE = """## Ollama Availability

**Status:** Unreachable.

This report is a **no-op** on hosts without a live Ollama. The live A/B
comparison is operator-evidence only and must be run on the M3 Ultra where
Ollama serves the local model.

To collect operator evidence:
1. On the M3 Ultra, run this script with a live Ollama.
2. Save the markdown report to the PR.
3. GM-panel review is the acceptance criterion for quality.
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ab_eval_harness_cli",
        description="A/B eval: identical prompts through Claude vs local Ollama.",
    )
    p.add_argument("--user-prompt", help="Single prompt to evaluate (diagnostic mode).")
    p.add_argument("--system-prompt", default=DEFAULT_SYSTEM, help="System prompt.")
    p.add_argument("--input-jsonl", help="TrainingPair JSONL slice (batch mode).")
    p.add_argument("--sample-size", type=int, default=None, help="Cap pairs evaluated.")
    p.add_argument("--output-md", help="Write the markdown report to this path.")
    p.add_argument("--genre", default="caverns_and_claudes", help="Genre tag.")
    return p


def _load_pairs(path: Path) -> list[TrainingPair]:
    """Parse a JSONL file into TrainingPair rows.

    Raises ValueError on any malformed/invalid line — untrusted boundary input
    is rejected loudly (rule #8/#11), never silently skipped.
    """
    pairs: list[TrainingPair] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno}: invalid JSON ({exc})") from exc
            try:
                pairs.append(TrainingPair.model_validate(obj))
            except pydantic.ValidationError as exc:
                raise ValueError(f"line {lineno}: not a TrainingPair ({exc})") from exc
    return pairs


def _build_clients() -> tuple[LlmClient, LlmClient]:
    """Construct the two send_stateless-capable backends.

    Resolves the TEA blocking finding (``build_llm_client()`` returns one
    client; the A/B needs two): Ollama comes from the factory with the backend
    env forced (same approach as ollama_latency_check.py); the Claude baseline
    is a direct ``ClaudeClient`` (the default ``anthropic_sdk`` backend is a
    ToolingLlmClient with no ``send_stateless``). Both are isinstance-guarded.
    """
    prior = os.environ.get(ENV_BACKEND)
    try:
        os.environ[ENV_BACKEND] = "ollama"
        ollama_client = build_llm_client()
    finally:
        if prior is None:
            os.environ.pop(ENV_BACKEND, None)
        else:
            os.environ[ENV_BACKEND] = prior

    claude_client: LlmClient = ClaudeClient()

    for label, client in (("claude", claude_client), ("ollama", ollama_client)):
        if not isinstance(client, LlmClient):
            raise ValueError(
                f"{label} backend is not an LlmClient (no send_stateless): "
                f"{type(client).__name__}. The anthropic_sdk ToolingLlmClient "
                f"is unsupported for the A/B harness."
            )
    return claude_client, ollama_client  # type: ignore[return-value]


def _emit(markdown: str, output_md: str | None) -> None:
    if output_md:
        Path(output_md).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 for --help, non-zero for bad args. Map bad args to
        # our config-error code so callers get a stable taxonomy.
        if exc.code in (0, None):
            return EXIT_PASS
        return EXIT_CONFIG_ERROR

    if args.sample_size is not None and args.sample_size <= 0:
        print(
            f"[ab_eval] config error: --sample-size must be > 0 (got {args.sample_size})",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    pairs: list[TrainingPair] | None = None
    if args.input_jsonl:
        path = Path(args.input_jsonl)
        if not path.is_file():
            print(f"[ab_eval] config error: no such file: {path}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        try:
            pairs = _load_pairs(path)
        except ValueError as exc:
            print(f"[ab_eval] config error: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        if not pairs:
            print("[ab_eval] config error: JSONL had no pairs", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    elif not args.user_prompt:
        print(
            "[ab_eval] config error: provide --user-prompt or --input-jsonl",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    try:
        claude_client, ollama_client = _build_clients()
    except (UnknownBackend, ValueError) as exc:
        print(f"[ab_eval] config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    harness = AbEvalHarness(
        claude_client=claude_client,
        ollama_client=ollama_client,
        system_prompt=args.system_prompt,
        genre=args.genre,
    )

    try:
        if pairs is not None:
            obj = asyncio.run(harness.eval_batch(pairs, sample_size=args.sample_size))
        else:
            obj = asyncio.run(harness.eval_pair(user_prompt=args.user_prompt))
    except OllamaClientError as exc:
        # Graceful operator-evidence no-op: not a crash. AC4.
        print(f"[ab_eval] ollama unreachable: {exc}", file=sys.stderr)
        _emit(OPERATOR_NOTE, args.output_md)
        return EXIT_OLLAMA_UNREACHABLE
    except LlmClientError as exc:
        print(f"[ab_eval] claude backend error: {exc}", file=sys.stderr)
        return EXIT_CLAUDE_ERROR

    _emit(obj.to_markdown(), args.output_md)
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
