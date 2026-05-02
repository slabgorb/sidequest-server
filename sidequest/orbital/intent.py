"""Intent dispatch for orbital chart messages.

Per spec §6.3: each intent → render new SVG → return OrbitalIntentResponse.
The Session holds the current scope so drill_out can return to its parent.

Pure function — does not touch the WebSocket transport. The handler
module under ``sidequest/handlers/`` (Task 15b) wires this into the
inbound message router.
"""

from __future__ import annotations

from sidequest.orbital.render import Scope, render_chart
from sidequest.protocol.orbital_intent import (
    DrillInIntent,
    DrillOutIntent,
    OrbitalIntent,
    OrbitalIntentResponse,
    ViewMapIntent,
)
from sidequest.server.session import Session


class OrbitalContentUnavailableError(RuntimeError):
    """Intent received for a session whose world has no orbital tier."""


def handle_orbital_intent(session: Session, intent: OrbitalIntent) -> OrbitalIntentResponse:
    """Resolve an orbital intent against the session's content + state.

    Side effect: updates ``session.orbital_scope`` so a subsequent
    drill_out resolves against the new center. Renders a fresh SVG and
    emits the ``chart.render`` OTEL span via ``render_chart``.
    """
    content = session.orbital_content
    if content is None:
        raise OrbitalContentUnavailableError(
            "session has no orbital content; world is not orbital-tier"
        )

    inner = intent.root
    if isinstance(inner, ViewMapIntent):
        scope = (
            Scope.system_root()
            if inner.scope == "system_root"
            else Scope(center_body_id=inner.scope)
        )
    elif isinstance(inner, DrillInIntent):
        scope = Scope(center_body_id=inner.body_id)
    elif isinstance(inner, DrillOutIntent):
        current = session.orbital_scope
        if current.center_body_id == "<root>":
            scope = Scope.system_root()
        else:
            body = content.orbits.bodies[current.center_body_id]
            scope = Scope(center_body_id=body.parent) if body.parent else Scope.system_root()
    else:  # pragma: no cover — exhaustive
        raise TypeError(f"Unknown orbital intent: {inner!r}")

    svg = render_chart(
        orbits=content.orbits,
        chart=content.chart,
        scope=scope,
        t_hours=session.clock.t_hours,
        party_at=session.party_body_id,
    )

    session.orbital_scope = scope

    actual_center = (
        scope.center_body_id if scope.center_body_id != "<root>" else _system_primary_id(content)
    )

    return OrbitalIntentResponse(
        scope_center=actual_center,
        svg=svg,
        t_hours=session.clock.t_hours,
        party_at=session.party_body_id,
    )


def _system_primary_id(content) -> str:
    return next(bid for bid, b in content.orbits.bodies.items() if b.parent is None)
