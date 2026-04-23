"""Legend types from legends.yaml.

Port of sidequest-genre/src/models/legends.rs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TerrainScar(BaseModel):
    """A physical scar on the landscape from a historical event."""

    model_config = {"extra": "forbid"}

    name: str
    description: str
    region: str = ""
    # "type" is a Python keyword — use alias
    scar_type: str = Field(alias="type", serialization_alias="type")

    model_config = {"extra": "forbid", "populate_by_name": True}


class FactionGrudge(BaseModel):
    """A grudge between two factions from a historical event."""

    model_config = {"extra": "forbid"}

    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    reason: str

    model_config = {"extra": "forbid", "populate_by_name": True}


class Legend(BaseModel):
    """A historical legend.

    heavy_metal/evropi legends (ported from Keith's 2010 campaign) author a
    richer shape — ``id``, ``culture``, ``period``, ``details``,
    ``notable_figures``, ``related_tropes`` — that Rust silently dropped.
    Accepted here as pass-through so the lore is preserved and narrators
    can pull from the full record.
    """

    model_config = {"extra": "forbid"}

    name: str
    summary: str = Field(default="", alias="summary")
    era: str = ""
    affected_cultures: list[str] = Field(default_factory=list)
    cultural_impact: str = ""
    faction_grudges: list[FactionGrudge] = Field(default_factory=list)
    lost_arts: list[str] = Field(default_factory=list)
    monuments: list[str] = Field(default_factory=list)
    terrain_scars: list[TerrainScar] = Field(default_factory=list)
    # evropi-authored extensions — unwired, pass-through until a consumer reads them
    id: str | None = None
    culture: str | None = None
    period: str | None = None
    details: str = ""
    notable_figures: list[str] = Field(default_factory=list)
    related_tropes: list[str] = Field(default_factory=list)

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
    }

    @classmethod
    def model_validate(cls, obj: object, **kwargs: Any) -> Legend:  # type: ignore[override]
        """Handle alias 'description' for summary field."""
        if isinstance(obj, dict) and "description" in obj and "summary" not in obj:
            obj = dict(obj)
            obj["summary"] = obj.pop("description")
        return super().model_validate(obj, **kwargs)
