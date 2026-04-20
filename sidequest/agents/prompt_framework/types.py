"""Core prompt framework types: AttentionZone, SectionCategory, RuleTier, PromptSection.

Port of sidequest-agents/src/prompt_framework/types.rs.
"""

from __future__ import annotations

from enum import Enum
from functools import total_ordering
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AttentionZone(str, Enum):
    """Attention zones ordered from highest-primacy to highest-recency.

    Maps to the proven attention pattern from ADR-009:
    - Primacy/Early: high attention (identity, SOUL, critical rules)
    - Valley: lower attention (lore, game state, background)
    - Late/Recency: high attention (checklist, user input)
    """

    Primacy = "primacy"
    Early = "early"
    Valley = "valley"
    Late = "late"
    Recency = "recency"

    def order(self) -> int:
        """Returns the sort order index (0 = first in prompt)."""
        _ORDER = {
            AttentionZone.Primacy: 0,
            AttentionZone.Early: 1,
            AttentionZone.Valley: 2,
            AttentionZone.Late: 3,
            AttentionZone.Recency: 4,
        }
        return _ORDER[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AttentionZone):
            return NotImplemented
        return self.order() < other.order()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, AttentionZone):
            return NotImplemented
        return self.order() <= other.order()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, AttentionZone):
            return NotImplemented
        return self.order() > other.order()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, AttentionZone):
            return NotImplemented
        return self.order() >= other.order()

    @classmethod
    def all_ordered(cls) -> list[AttentionZone]:
        """Returns all zones in prompt assembly order."""
        return [
            cls.Primacy,
            cls.Early,
            cls.Valley,
            cls.Late,
            cls.Recency,
        ]


class SectionCategory(str, Enum):
    """Prompt section categories — extensible as new agent types are added."""

    Identity = "identity"
    Guardrail = "guardrail"
    Soul = "soul"
    Genre = "genre"
    State = "state"
    Action = "action"
    Format = "format"
    Context = "context"
    Role = "role"


class RuleTier(str, Enum):
    """Three-tier rule taxonomy for agent system prompts.

    Maps to the Python RuleTier / RuleTaxonomy:
    - Critical: always enforced, all agents (agency, output-format, no-metagame)
    - Firm: agent-specific behavioral rules (living-world, genre-truth)
    - Coherence: stylistic guidelines (brevity, sensory-grounding)
    """

    Critical = "critical"
    Firm = "firm"
    Coherence = "coherence"


class PromptSection(BaseModel):
    """A named, categorized, zone-labeled unit of prompt content.

    Frozen (immutable) after construction. Token count is derived from content.
    Port of sidequest-agents/src/prompt_framework/types.rs::PromptSection.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    name: str
    category: SectionCategory
    zone: AttentionZone
    content: str
    source: str | None = Field(default=None)

    @classmethod
    def new(
        cls,
        name: str,
        content: str,
        zone: AttentionZone,
        category: SectionCategory,
    ) -> PromptSection:
        """Create a new prompt section."""
        return cls(name=name, content=content, zone=zone, category=category)

    @classmethod
    def with_source(
        cls,
        name: str,
        content: str,
        zone: AttentionZone,
        category: SectionCategory,
        source: str,
    ) -> PromptSection:
        """Create a new prompt section with a source tag."""
        return cls(name=name, content=content, zone=zone, category=category, source=source)

    def token_estimate(self) -> int:
        """Approximate token count (word count as proxy)."""
        return len(self.content.split())

    def is_empty(self) -> bool:
        """Returns true if the section has no content."""
        return not self.content.strip()
