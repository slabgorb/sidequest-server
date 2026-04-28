"""Merchant spans — context injection and transactions."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_MERCHANT_CONTEXT_INJECTED = "merchant.context_injected"
SPAN_MERCHANT_TRANSACTION = "merchant.transaction"

FLAT_ONLY_SPANS.update({SPAN_MERCHANT_CONTEXT_INJECTED, SPAN_MERCHANT_TRANSACTION})
