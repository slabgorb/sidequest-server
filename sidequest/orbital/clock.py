"""Story-time clock primitive.

The clock stores absolute hours from a world-defined epoch (`epoch_days: 0`
in `orbits.yaml`). It advances *only* via beats — see `sidequest.orbital.beats`.

Per the orbital map spec (§3.1): internal unit is hours; display formatting
lives in `sidequest.orbital.display`. Standard Day = 24h.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Clock:
    """Story-time clock for a single session.

    `t_hours` is monotonic non-decreasing; `advance(hours)` is the only
    mutation method. Direct mutation of `t_hours` is technically possible
    but discouraged — go through `advance()` so the invariant holds.
    """

    t_hours: float = 0.0

    @property
    def t_days(self) -> float:
        return self.t_hours / 24.0

    def advance(self, hours: float) -> None:
        """Advance the clock by `hours`. Negative values raise ValueError.

        Zero is allowed (a no-op beat is legal — the engine still emits
        the OTEL span for it, since recording the *attempt* matters).
        """
        if hours < 0:
            raise ValueError(f"Clock cannot advance by negative hours: {hours!r}")
        self.t_hours += hours
