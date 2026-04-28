"""Barrier spans — sealed-letter activation and resolution."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_BARRIER_ACTIVATED = "barrier.activated"
SPAN_BARRIER_RESOLVED = "barrier.resolved"

FLAT_ONLY_SPANS.update({SPAN_BARRIER_ACTIVATED, SPAN_BARRIER_RESOLVED})
