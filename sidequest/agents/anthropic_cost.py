"""Anthropic API pricing — pure functions.

Pricing snapshot from 2026-05-15. Update when Anthropic publishes a change;
unit tests pin the constants so a change is forced through review.
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownModel(ValueError):
    """compute_cost_usd was passed a model id not in the pricing table."""


@dataclass(frozen=True, slots=True)
class ModelPricing:
    model: str
    input_per_mtok_usd: float
    output_per_mtok_usd: float
    cached_input_read_per_mtok_usd: float
    cached_input_write_per_mtok_usd: float


_PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(
        model="claude-sonnet-4-6",
        input_per_mtok_usd=3.0,
        output_per_mtok_usd=15.0,
        cached_input_read_per_mtok_usd=0.30,
        cached_input_write_per_mtok_usd=3.75,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        model="claude-haiku-4-5-20251001",
        input_per_mtok_usd=1.0,
        output_per_mtok_usd=5.0,
        cached_input_read_per_mtok_usd=0.10,
        cached_input_write_per_mtok_usd=1.25,
    ),
    "claude-opus-4-7": ModelPricing(
        model="claude-opus-4-7",
        input_per_mtok_usd=15.0,
        output_per_mtok_usd=75.0,
        cached_input_read_per_mtok_usd=1.50,
        cached_input_write_per_mtok_usd=18.75,
    ),
}


def model_pricing(model: str) -> ModelPricing:
    try:
        return _PRICING[model]
    except KeyError as exc:
        raise UnknownModel(f"No pricing entry for model {model!r}") from exc


def compute_cost_usd(
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_read_tokens: int,
    cached_input_write_tokens: int,
    model: str,
) -> float:
    """Sum the per-bucket cost for one API call.

    `input_tokens` must be fresh (uncached) input only — matches the Anthropic
    SDK's `usage.input_tokens` semantics, which excludes cached buckets.
    """
    p = model_pricing(model)
    return (
        input_tokens * p.input_per_mtok_usd
        + output_tokens * p.output_per_mtok_usd
        + cached_input_read_tokens * p.cached_input_read_per_mtok_usd
        + cached_input_write_tokens * p.cached_input_write_per_mtok_usd
    ) / 1_000_000
