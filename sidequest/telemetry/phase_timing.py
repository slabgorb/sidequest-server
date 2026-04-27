"""PhaseTimings — per-turn wall-clock phase accumulator.

Attached to TurnContext. Records elapsed-ms for each named phase via a
context manager. Survives exceptions inside phase blocks (try/finally in
__exit__). Repeated phase names accumulate additively. After mark_done()
the instance is finalized; subsequent .phase() calls raise RuntimeError.

The class is a passive accumulator. It does not interpret, threshold,
log, or alert. All semantic decisions live downstream (validator, panel).

See docs/superpowers/specs/2026-04-26-turn-pipeline-phase-timing-design.md.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar


class PhaseTimings:
    """Per-turn phase-timing accumulator. One instance per turn."""

    NULL: ClassVar[PhaseTimings]

    def __init__(self, *, action_received_monotonic: float) -> None:
        # Touch monotonic at construction time so timings start from a
        # consistent reference point (and so tests can mock a single
        # __init__→__enter__→__exit__ sequence).
        _ = time.monotonic()
        self._start: float = action_received_monotonic
        self._totals_ms: dict[str, int] = {}
        self._call_counts: dict[str, int] = {}
        self._total_duration_ms: int | None = None
        self._finalized: bool = False

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if self._finalized:
            raise RuntimeError("PhaseTimings already finalized")
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            self._totals_ms[name] = self._totals_ms.get(name, 0) + elapsed_ms
            self._call_counts[name] = self._call_counts.get(name, 0) + 1

    def mark_done(self) -> None:
        if self._finalized:
            return
        self._total_duration_ms = round((time.monotonic() - self._start) * 1000)
        self._finalized = True

    @property
    def total_ms(self) -> int:
        if self._total_duration_ms is None:
            return round((time.monotonic() - self._start) * 1000)
        return self._total_duration_ms

    @property
    def phase_call_counts(self) -> dict[str, int]:
        return dict(self._call_counts)

    @property
    def unaccounted_ms(self) -> int:
        accounted = sum(self._totals_ms.values())
        return max(0, self.total_ms - accounted)

    def to_dict(self) -> dict[str, int]:
        return dict(self._totals_ms)


class _NullPhaseTimings(PhaseTimings):
    """No-op singleton for fixtures and partial mocks."""

    def __init__(self) -> None:  # noqa: D401 — explicit override
        super().__init__(action_received_monotonic=0.0)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        yield

    def mark_done(self) -> None:
        return

    @property
    def total_ms(self) -> int:
        return 0

    @property
    def unaccounted_ms(self) -> int:
        return 0

    def to_dict(self) -> dict[str, int]:
        return {}


PhaseTimings.NULL = _NullPhaseTimings()
