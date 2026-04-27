"""Unit + property tests for PhaseTimings accumulator."""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from sidequest.telemetry.phase_timing import PhaseTimings


def test_phase_records_elapsed_ms() -> None:
    """A single .phase() block records the elapsed time in to_dict()."""
    times = iter([100.0, 100.0, 100.5])  # __init__, __enter__, __exit__
    with patch("sidequest.telemetry.phase_timing.time.monotonic", side_effect=lambda: next(times)):
        timings = PhaseTimings(action_received_monotonic=100.0)
        with timings.phase("preprocess_llm"):
            pass
        assert timings.to_dict() == {"preprocess_llm": 500}


def test_phase_accumulates_on_repeat() -> None:
    """Two .phase('X') blocks sum into one entry; call count tracks both."""
    times = iter([0.0, 0.0, 0.1, 0.5, 0.7])  # __init__, enter1, exit1, enter2, exit2
    with patch("sidequest.telemetry.phase_timing.time.monotonic", side_effect=lambda: next(times)):
        timings = PhaseTimings(action_received_monotonic=0.0)
        with timings.phase("X"):
            pass
        with timings.phase("X"):
            pass
        assert timings.to_dict() == {"X": 100 + 200}
        assert timings.phase_call_counts == {"X": 2}


def test_phase_records_on_exception() -> None:
    """Exception inside a phase block: elapsed still recorded, exception propagates."""
    times = iter([0.0, 0.0, 0.4])  # __init__, enter, exit
    with patch("sidequest.telemetry.phase_timing.time.monotonic", side_effect=lambda: next(times)):
        timings = PhaseTimings(action_received_monotonic=0.0)
        with pytest.raises(ValueError, match="boom"), timings.phase("preprocess_llm"):
            raise ValueError("boom")
        assert timings.to_dict() == {"preprocess_llm": 400}


def test_finalized_timer_rejects_writes() -> None:
    """After mark_done(), .phase() raises RuntimeError."""
    timings = PhaseTimings(action_received_monotonic=0.0)
    timings.mark_done()
    with pytest.raises(RuntimeError, match="finalized"), timings.phase("X"):
        pass


def test_null_singleton_is_noop() -> None:
    """PhaseTimings.NULL.phase() is a no-op; to_dict() is empty."""
    with PhaseTimings.NULL.phase("anything"):
        pass
    assert PhaseTimings.NULL.to_dict() == {}
    assert PhaseTimings.NULL.total_ms == 0
    assert PhaseTimings.NULL.unaccounted_ms == 0
    PhaseTimings.NULL.mark_done()  # no-op, no error


def test_unaccounted_ms_computed() -> None:
    """unaccounted_ms = total - sum(phases); always >= 0."""
    times = iter([0.0, 0.0, 0.1, 1.0])  # __init__, enter, exit, mark_done
    with patch("sidequest.telemetry.phase_timing.time.monotonic", side_effect=lambda: next(times)):
        timings = PhaseTimings(action_received_monotonic=0.0)
        with timings.phase("preprocess_llm"):
            pass
        timings.mark_done()
    assert timings.to_dict() == {"preprocess_llm": 100}
    assert timings.total_ms == 1000
    assert timings.unaccounted_ms == 900


def test_sum_of_phases_approximates_total() -> None:
    """Over 100 randomized turn shapes: sum(phases) + unaccounted == total, all >= 0."""
    rng = random.Random(0xC0FFEE)
    for _ in range(100):
        # Build a sequence of (start, exit) monotonic timestamps for K phases.
        # All phases happen between t=0 (start) and t=total (mark_done).
        k = rng.randint(0, 8)
        total_s = rng.uniform(0.5, 5.0)
        times: list[float] = [0.0]  # __init__
        cursor = 0.0
        for _ in range(k):
            enter = cursor + rng.uniform(0.0, 0.05)
            exit_ = enter + rng.uniform(0.0, 0.4)
            times.extend([enter, exit_])
            cursor = exit_
            if cursor > total_s:
                break
        # Ensure mark_done's monotonic >= the last phase exit so total >= accounted.
        times.append(max(total_s, cursor))  # mark_done

        it = iter(times)
        with patch(
            "sidequest.telemetry.phase_timing.time.monotonic",
            side_effect=lambda it=it: next(it),
        ):
            timings = PhaseTimings(action_received_monotonic=0.0)
            phase_count = (len(times) - 2) // 2
            for i in range(phase_count):
                with timings.phase(f"p{i}"):
                    pass
            timings.mark_done()

        accounted = sum(timings.to_dict().values())
        assert timings.total_ms >= 0
        assert timings.unaccounted_ms >= 0
        assert accounted + timings.unaccounted_ms == timings.total_ms
