"""Wire protocol for orbital chart intents.

Per spec §6.3: UI sends intents over the existing WebSocket transport
(ADR-038); server returns rendered SVG (or scene update for commit_route,
which lives in Plan 2).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class _IntentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ViewMapIntent(_IntentBase):
    kind: Literal["view_map"] = "view_map"
    scope: str = "system_root"  # "system_root" or a body_id


class DrillInIntent(_IntentBase):
    kind: Literal["drill_in"] = "drill_in"
    body_id: str


class DrillOutIntent(_IntentBase):
    kind: Literal["drill_out"] = "drill_out"


_AnyIntent = Annotated[
    ViewMapIntent | DrillInIntent | DrillOutIntent,
    Field(discriminator="kind"),
]


class OrbitalIntent(RootModel[_AnyIntent]):
    """Polymorphic root for any orbital chart intent message."""


class ConjunctionEventPayload(BaseModel):
    """Wire shape of a scheduled conjunction event for the chart HUD strip."""

    model_config = ConfigDict(extra="forbid")
    body_a_id: str
    body_b_id: str
    label: str
    t_hours_event: float
    t_hours_until: float


class OrbitalIntentResponse(BaseModel):
    """Server response to an orbital intent — full SVG + scope metadata.

    Fields beyond `svg` drive the React-side HUD overlays per spec §10:
      - `t_hours` + `epoch_days`: top strip stardate readout
      - `next_conjunction`: bottom strip countdown (None hides the panel)
    """

    model_config = ConfigDict(extra="forbid")
    scope_center: str
    svg: str
    t_hours: float
    epoch_days: float = 0.0
    party_at: str | None = None
    next_conjunction: ConjunctionEventPayload | None = None
