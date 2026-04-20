"""Character-related types: archetypes, creation scenes, visual style.

Port of sidequest-genre/src/models/character.rs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from sidequest.genre.models.ocean import OceanProfile


class NpcArchetype(BaseModel):
    """An NPC archetype template.

    No extra="forbid" — genre packs may add genre-specific fields (role, morale, etc.)
    that are not in the base struct. Rust serde silently ignores unknown fields here.
    """

    model_config = {"extra": "allow"}

    name: str
    description: str
    personality_traits: list[str] = Field(default_factory=list)
    typical_classes: list[str] = Field(default_factory=list)
    typical_races: list[str] = Field(default_factory=list)
    stat_ranges: dict[str, list[int]] = Field(default_factory=dict)
    inventory_hints: list[str] = Field(default_factory=list)
    dialogue_quirks: list[str] = Field(default_factory=list)
    disposition_default: int = 0
    catalog_items: list[str] = Field(default_factory=list)
    ocean: OceanProfile | None = None


class MechanicalEffects(BaseModel):
    """Mechanical effects of a character creation choice or scene-level directive."""

    model_config = {"extra": "forbid"}

    class_hint: str | None = None
    race_hint: str | None = None
    mutation_hint: str | None = None
    item_hint: str | None = None
    affinity_hint: str | None = None
    training_hint: str | None = None
    background: str | None = None
    personality_trait: str | None = None
    emotional_state: str | None = None
    relationship: str | None = None
    goals: str | None = None
    allows_freeform: bool | None = None
    rig_type_hint: str | None = None
    rig_trait: str | None = None
    catch_phrase: str | None = Field(default=None, alias="catch", serialization_alias="catch")
    stat_bonuses: dict[str, int] = Field(default_factory=dict)
    pronoun_hint: str | None = None
    stat_generation: str | None = None
    equipment_generation: str | None = None
    jungian_hint: str | None = None
    rpg_role_hint: str | None = None

    model_config = {"extra": "forbid", "populate_by_name": True}


class CharCreationChoice(BaseModel):
    """A choice within a character creation scene."""

    model_config = {"extra": "forbid"}

    label: str
    description: str
    mechanical_effects: MechanicalEffects


class CharCreationScene(BaseModel):
    """A character creation scene with narrative choices."""

    model_config = {"extra": "forbid"}

    id: str
    title: str
    narration: str
    choices: list[CharCreationChoice] = Field(default_factory=list)
    loading_text: str | None = None
    allows_freeform: bool | None = None
    hook_prompt: str | None = None
    mechanical_effects: MechanicalEffects | None = None


class BackstoryTables(BaseModel):
    """Random backstory composition tables loaded from backstory_tables.yaml."""

    # No deny_unknown_fields — deserializer extracts template + dynamic table keys
    template: str
    tables: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def model_validate(cls, obj: object, **kwargs: Any) -> "BackstoryTables":  # type: ignore[override]
        """Extract template and remaining string-list keys as tables."""
        if isinstance(obj, dict):
            data: dict[str, Any] = dict(obj)
            template = data.get("template", "")
            tables: dict[str, list[str]] = {}
            for k, v in data.items():
                if k == "template":
                    continue
                if isinstance(v, list) and v and isinstance(v[0], str):
                    tables[k] = [str(x) for x in v]
            return cls(template=template, tables=tables)
        return super().model_validate(obj, **kwargs)


class EquipmentTables(BaseModel):
    """Random equipment generation tables loaded from equipment_tables.yaml."""

    model_config = {"extra": "forbid"}

    tables: dict[str, list[str]] = Field(default_factory=dict)
    rolls_per_slot: dict[str, int] = Field(default_factory=dict)


class VisualStyle(BaseModel):
    """Image generation style configuration.

    Intentionally no extra="forbid" — genre packs may add flavor fields.
    """

    # Note: No extra="forbid" per Rust comment (visual_style_accepts_extra_fields)
    model_config = {"extra": "allow"}

    positive_suffix: str
    negative_prompt: str
    preferred_model: str
    base_seed: int
    visual_tag_overrides: dict[str, str] = Field(default_factory=dict)
    lora: str | None = None
    lora_trigger: str | None = None
    lora_scale: float | None = None

    @field_validator("lora_scale", mode="before")
    @classmethod
    def _validate_lora_scale(cls, v: Any) -> Any:
        if v is None:
            return None
        v = float(v)
        import math
        if not math.isfinite(v):
            raise ValueError(f"lora_scale must be a finite number in [0.0, 2.0], got {v}")
        if v < 0.0:
            raise ValueError(f"lora_scale must be >= 0.0, got {v}")
        if v > 2.0:
            raise ValueError(f"lora_scale must be <= 2.0, got {v}")
        return v
