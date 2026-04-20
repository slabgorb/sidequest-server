"""Prompt framework — attention-zone prompt composition for Claude CLI agents.

Ports the attention-zone system (ADR-009) from Rust to Python.

Public surface:
- AttentionZone, SectionCategory, RuleTier, PromptSection  (from types.py)
- SoulData, SoulPrinciple, parse_soul_md                    (from soul.py)
- PromptComposer (Protocol), PromptRegistry                 (from core.py)
"""

from __future__ import annotations

from sidequest.agents.prompt_framework.core import PromptComposer, PromptRegistry
from sidequest.agents.prompt_framework.soul import SoulData, SoulPrinciple, parse_soul_md
from sidequest.agents.prompt_framework.types import (
    AttentionZone,
    PromptSection,
    RuleTier,
    SectionCategory,
)

__all__ = [
    "AttentionZone",
    "PromptComposer",
    "PromptRegistry",
    "PromptSection",
    "RuleTier",
    "SectionCategory",
    "SoulData",
    "SoulPrinciple",
    "parse_soul_md",
]
