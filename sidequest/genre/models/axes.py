"""Narrative axis configuration from axes.yaml.

Port of sidequest-genre/src/models/axes.rs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AxisDefinition(BaseModel):
    """A single narrative axis definition."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    description: str
    poles: list[str]
    default: float


class AxisPreset(BaseModel):
    """A preset combination of axis values."""

    model_config = {"extra": "forbid"}

    name: str
    description: str
    values: dict[str, float] = Field(default_factory=dict)


class AxesConfig(BaseModel):
    """Narrative axis configuration."""

    model_config = {"extra": "forbid"}

    definitions: list[AxisDefinition]
    modifiers: dict[str, dict[str, str]] = Field(default_factory=dict)
    presets: list[AxisPreset] = Field(default_factory=list)
