"""Pydantic models for orbits.yaml and chart.yaml.

Per spec §2.1–§2.2: orbits.yaml is the plotter's only input (mechanics);
chart.yaml is renderer-only (flavor); they live in the per-world content
directory and are loaded by `sidequest.orbital.loader`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TravelRealism(StrEnum):
    NARRATIVE = "narrative"
    HYBRID = "hybrid"
    ORBITAL = "orbital"


class BodyType(StrEnum):
    STAR = "star"
    COMPANION = "companion"
    HABITAT = "habitat"
    ARC_BELT = "arc_belt"
    GATE = "gate"
    WRECK = "wreck"


class ClockConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_days: float = 0.0


class TravelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    realism: TravelRealism = TravelRealism.NARRATIVE
    travel_speed_factor: float = Field(default=1.0, gt=0.0)
    danger_density: float = Field(default=0.0, ge=0.0)
    hazard_arc_density: float = Field(default=0.0, ge=0.0)


class BodyDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: BodyType
    parent: str | None = None
    semi_major_au: float | None = None
    period_days: float | None = None
    epoch_phase_deg: float | None = None
    eccentricity: float = 0.0
    arc_extent_deg: float | None = None
    hazard: bool = False
    hazard_table: str | None = None
    label: str | None = None
    label_color: str | None = None

    @model_validator(mode="after")
    def _validate_orbital_params(self) -> BodyDef:
        if self.parent is not None:
            for fld in ("semi_major_au", "period_days", "epoch_phase_deg"):
                if getattr(self, fld) is None:
                    raise ValueError(f"body with parent={self.parent!r} requires {fld}; got None")
        if self.type == BodyType.ARC_BELT and self.arc_extent_deg is None:
            raise ValueError("body with type=arc_belt requires arc_extent_deg; got None")
        return self


class OrbitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    clock: ClockConfig
    travel: TravelConfig
    bodies: dict[str, BodyDef]

    @model_validator(mode="after")
    def _validate_parent_refs(self) -> OrbitsConfig:
        ids = set(self.bodies.keys())
        for body_id, body in self.bodies.items():
            if body.parent is not None and body.parent not in ids:
                raise ValueError(
                    f"body {body_id!r} has unknown parent {body.parent!r}; "
                    f"available bodies: {sorted(ids)}"
                )
        return self


class Annotation(BaseModel):
    """Chart-only flavor element. `kind` selects renderer behavior;
    other fields are per-kind (validated leniently — renderer asserts
    what it needs)."""

    model_config = ConfigDict(extra="forbid")
    kind: str
    text: str | None = None
    caption: str | None = None
    curve_along: str | None = None
    at: dict[str, Any] | None = None
    style: str | None = None
    body_ref: str | None = None
    bearings: list[float] | None = None
    label: str | None = None


class ChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    annotations: list[Annotation] = Field(default_factory=list)
