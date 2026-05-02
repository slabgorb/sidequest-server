"""Per-slug Session aggregate — strangler-fig over the post-port server tier.

Owned by SessionRoom; constructed when the room's snapshot binds.
Reads/writes session state through GameSnapshot (the persistent boundary).

Today this class owns only the orbital clock and scene-end coordination.
Future migrations move more behavior inward one method at a time.

Per spec docs/superpowers/specs/2026-05-01-session-aggregate-design.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sidequest.orbital.beats import StoryBeat, StoryBeatKind, advance_clock_via_beat
from sidequest.orbital.clock import Clock
from sidequest.orbital.render import Scope
from sidequest.server.status_clear import clear_scratch_on_scene_end

if TYPE_CHECKING:
    from sidequest.game.session import GameSnapshot
    from sidequest.orbital.loader import OrbitalContent


class Session:
    """Per-slug behavior aggregate.

    Constructed by ``SessionRoom.bind_world`` (and any future re-bind
    paths) over the canonical ``GameSnapshot``. The snapshot is the
    persistence boundary; ``Session`` is a thin behavior layer over it.
    """

    def __init__(
        self,
        snapshot: GameSnapshot,
        *,
        orbital_content: OrbitalContent | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._orbital_content = orbital_content
        # Orbital scope is transient session UI state — defaults to system
        # root on each connect rather than persisting across reconnects.
        self._orbital_scope: Scope | None = None

    @property
    def clock(self) -> Clock:
        """Read-only Clock view over ``snapshot.clock_t_hours``.

        Mutations on the returned Clock do NOT persist. To advance the
        clock, call ``advance_via_beat`` (which validates the beat,
        emits the OTEL span, and writes back to the snapshot).
        """
        return Clock(t_hours=self._snapshot.clock_t_hours)

    def advance_via_beat(self, beat: StoryBeat) -> float:
        """Advance the clock per the beat. Persists to snapshot. Emits span."""
        local = Clock(t_hours=self._snapshot.clock_t_hours)
        duration = advance_clock_via_beat(local, beat)
        self._snapshot.clock_t_hours = local.t_hours
        return duration

    def end_scene(self, reason: str, *, turn: int) -> None:
        """Scene-end signal: scratch sweep first, then ENCOUNTER beat.

        Called by encounter-resolution sites (narrator beat resolution,
        dice resolution, yielded). The location_change site stays on
        ``clear_scratch_on_scene_end`` directly — not a scene end
        semantically.
        """
        clear_scratch_on_scene_end(self._snapshot, reason=reason, turn=turn)
        self.advance_via_beat(
            StoryBeat(kind=StoryBeatKind.ENCOUNTER, trigger=f"scene-{reason}")
        )

    # ------------------------------------------------------------------
    # Orbital map (Task 15) — content + scope state for the chart UI.
    # ------------------------------------------------------------------

    @property
    def orbital_content(self) -> OrbitalContent | None:
        """Loaded ``orbits.yaml`` + ``chart.yaml`` for the bound world.

        ``None`` for worlds without an orbital tier (caverns_and_claudes,
        victoria, etc.). Set once at room bind time; never mutated.
        """
        return self._orbital_content

    @property
    def orbital_scope(self) -> Scope:
        """Current chart scope — defaults to system root on first read."""
        return self._orbital_scope or Scope.system_root()

    @orbital_scope.setter
    def orbital_scope(self, scope: Scope) -> None:
        self._orbital_scope = scope

    @property
    def party_body_id(self) -> str | None:
        """Party's orbital body id (from ``orbits.yaml``), or ``None``."""
        return self._snapshot.party_body_id
