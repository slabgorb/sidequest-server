"""Display formatting for durations.

Per spec §3.1: internal unit is hours; display picks scale-appropriate
units. Engine never returns formatted strings — formatters live here so
callers (UI, CLI, narrator scene context framing) can convert at the edge.
"""
from __future__ import annotations


def format_duration(hours: float) -> str:
    """Format `hours` per spec §3.1 thresholds.

    Boundaries:
      < 1h          → minutes
      1h–24h        → hours
      24h–336h      (1–14 days)        → days
      337h–2160h    (~2–13 weeks)      → weeks
      2161h–17519h  (~3–24 months)     → months
      ≥17520h                          → years

    Plural-aware ("1 day" vs "2 days"). Rounds to nearest integer of the
    chosen unit. Thresholds are evaluated against raw `hours` so that the
    spec's day/week/month boundaries (336h / 2160h / 17520h) hold even
    when the per-unit value rounds across a boundary.
    """
    if hours < 0:
        raise ValueError(f"format_duration cannot accept negative hours: {hours!r}")

    if hours < 1.0:
        n = max(1, round(hours * 60))
        return _plural(n, "minute")
    if hours < 24.0:
        n = round(hours)
        return _plural(n, "hour")
    if hours <= 336.0:
        n = round(hours / 24.0)
        return _plural(n, "day")
    if hours <= 2160.0:
        n = round(hours / 24.0 / 7.0)
        return _plural(n, "week")
    if hours < 17520.0:
        n = round(hours / 24.0 / 30.0)
        return _plural(n, "month")
    n = round(hours / 24.0 / 365.0)
    return _plural(n, "year")


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"
