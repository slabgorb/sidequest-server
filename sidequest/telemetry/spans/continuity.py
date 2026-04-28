"""Continuity spans — LLM-driven validation of narrative consistency."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_CONTINUITY_LLM_VALIDATION = "continuity.llm_validation"

FLAT_ONLY_SPANS.add(SPAN_CONTINUITY_LLM_VALIDATION)
