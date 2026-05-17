"""Shared test infra for the dungeon test package.

`reset_otel_provider` resolves a deterministic test-isolation blocker:
`test_commit_and_ledger_emit_spans` calls `trace.set_tracer_provider()`
directly, but OTEL 1.x gates that behind a once-only guard
(`_TRACER_PROVIDER_SET_ONCE`). When `tests/agents/conftest.py`'s
`init_tracer()` fired earlier in a full-suite session, the first
provider wins and every later `set_tracer_provider()` is silently
dropped — so the test's `_Capture` exporter is never installed and its
span assertions fail. Resetting both the stored provider reference and
the `Once._done` flag before the test re-installs the provider makes the
call effective again. Test-infra only — production `init_tracer()` is
correct (it is the single legitimate set_tracer_provider caller at
runtime; this only matters when many tests each install their own
in-memory provider in one process).

This mirrors the established private-API helper at
`tests/telemetry/test_spans.py:_reset_otel_provider`. It is duplicated
here (a sibling package-local helper) rather than cross-imported: the
underscore-prefixed test-module helper is intentionally module-private
and must not be reached across test modules.
"""

from __future__ import annotations

from opentelemetry import trace


def reset_otel_provider() -> None:
    """Reset the OTEL global provider so set_tracer_provider() is not a no-op.

    OTEL 1.x gates set_tracer_provider behind a Once guard
    (_TRACER_PROVIDER_SET_ONCE). In a test session the first call wins and
    all subsequent calls are silently dropped. We reset both the stored
    provider reference and the Once._done flag before a test that calls
    set_tracer_provider directly. Private-API access — update if the OTEL
    SDK moves the guard.
    """
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    once = getattr(trace, "_TRACER_PROVIDER_SET_ONCE", None)
    if once is not None:
        once._done = False
