"""Image pacing throttle (ADR-050).

Time-based suppression at the render-dispatch layer. Prevents image flooding
during rapid mechanical turn sequences (combat rounds, skill checks, quick
back-and-forth dialogue) by enforcing a minimum cooldown window between
dispatched renders. Suppressed renders never reach the daemon — the throttle
protects GPU resources, not just the client experience.

Defaults per ADR-050:
- Solo:        30s cooldown
- Multiplayer: 60s cooldown (turns resolve faster in group play)

A cooldown of 0 disables throttling. The GM force-override (``force_render``)
bypasses the cooldown but does NOT reset the timer — organic renders continue
their normal cadence after a forced render.

Per-instance state. Each WebSocket session owns its own throttle (held on
``_SessionData.image_pacing_throttle``). This is safe under single-worker
uvicorn; if/when we run multi-worker, throttle state would split per-process
and need a shared backing store (e.g. Redis or a shared memory dict).

Re-implementation history: this module was originally Rust at
``sidequest-api/src/render_integration.rs`` (archived at
https://github.com/slabgorb/sidequest-api). The Rust impl did not survive
ADR-082's Python port; restored 2026-04-26 as a 1:1 Python port of the
public surface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# ADR-050 defaults — keep these as module constants so tests can import them
# directly rather than hardcoding magic numbers.
DEFAULT_SOLO_COOLDOWN_SECONDS = 30
DEFAULT_MULTIPLAYER_COOLDOWN_SECONDS = 60


@dataclass
class ThrottleDecision:
    """Result of a single throttle consultation.

    ``allowed`` — whether the render should proceed.
    ``reason`` — short snake_case label suitable for OTEL ``reason=`` attr.
    ``cooldown_remaining_seconds`` — non-negative; 0 when allowed or cooldown
    was already 0 (throttle disabled).
    """

    allowed: bool
    reason: str
    cooldown_remaining_seconds: int


@dataclass
class ImagePacingThrottle:
    """Time-based render dispatch suppressor.

    The state machine is intentionally tiny: a cooldown duration and a
    timestamp of the last dispatched render. Both ``should_render`` and
    ``record_render`` are O(1) and synchronous; safe to call from the
    same event loop that drives the dispatch path.

    ``last_render_monotonic`` uses :func:`time.monotonic` so we cannot be
    fooled by wall-clock adjustments mid-session.
    """

    cooldown_seconds: int
    last_render_monotonic: float | None = field(default=None)

    # ---- constructors ----

    @classmethod
    def for_solo(cls) -> ImagePacingThrottle:
        return cls(cooldown_seconds=DEFAULT_SOLO_COOLDOWN_SECONDS)

    @classmethod
    def for_multiplayer(cls) -> ImagePacingThrottle:
        return cls(cooldown_seconds=DEFAULT_MULTIPLAYER_COOLDOWN_SECONDS)

    @classmethod
    def default_for_player_count(cls, n: int) -> ImagePacingThrottle:
        """ADR-050 default selector: solo if 1 or fewer players, MP otherwise."""
        return cls.for_solo() if n <= 1 else cls.for_multiplayer()

    # ---- core decision API ----

    def should_render(self, now: float | None = None) -> ThrottleDecision:
        """Return the dispatch decision for the current moment.

        Pure: does NOT mutate state. Callers must invoke
        :meth:`record_render` after the render is actually dispatched so
        the next call sees the updated timestamp.

        Decision reasons:
          - ``"throttle_disabled"`` — cooldown is 0; always allowed.
          - ``"first_render"``      — no prior render recorded; allowed.
          - ``"cooldown_elapsed"``  — cooldown window has passed; allowed.
          - ``"cooldown_active"``   — within cooldown window; suppressed.
        """
        now_t = time.monotonic() if now is None else now
        if self.cooldown_seconds <= 0:
            return ThrottleDecision(
                allowed=True,
                reason="throttle_disabled",
                cooldown_remaining_seconds=0,
            )
        if self.last_render_monotonic is None:
            return ThrottleDecision(
                allowed=True,
                reason="first_render",
                cooldown_remaining_seconds=0,
            )
        elapsed = now_t - self.last_render_monotonic
        if elapsed >= self.cooldown_seconds:
            return ThrottleDecision(
                allowed=True,
                reason="cooldown_elapsed",
                cooldown_remaining_seconds=0,
            )
        remaining = max(0, int(self.cooldown_seconds - elapsed))
        return ThrottleDecision(
            allowed=False,
            reason="cooldown_active",
            cooldown_remaining_seconds=remaining,
        )

    def record_render(self, now: float | None = None) -> None:
        """Mark a render as dispatched; the cooldown window restarts here."""
        self.last_render_monotonic = time.monotonic() if now is None else now

    def force_render(self) -> ThrottleDecision:
        """GM force override — always allowed, does NOT reset the timer.

        Per ADR-050: "The force-override does not reset the cooldown
        timer — the window continues from the previous render, so the
        GM's manual trigger doesn't shorten the next organic cooldown."

        Callers MUST NOT call ``record_render`` after a forced render
        if they want the original cadence preserved.
        """
        return ThrottleDecision(
            allowed=True,
            reason="forced",
            cooldown_remaining_seconds=0,
        )

    # ---- mid-session config (ADR-050 future slider hook) ----

    def set_cooldown_seconds(self, seconds: int) -> None:
        """Update the cooldown duration mid-session.

        Does not retroactively expire an in-flight cooldown — if the new
        value is shorter than the elapsed time, the next ``should_render``
        will see it as ``cooldown_elapsed``; if longer, the existing window
        continues with the new ceiling.
        """
        if seconds < 0:
            raise ValueError(f"cooldown_seconds must be >= 0, got {seconds}")
        self.cooldown_seconds = seconds
