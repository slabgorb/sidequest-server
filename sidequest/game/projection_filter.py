"""Per-player projection filter.

The spec explicitly defers concrete filter rules. This module ships the
protocol + a pass-through default. Asymmetric-info rules land in follow-up
work without touching wiring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sidequest.game.event_log import EventRow


@dataclass(frozen=True)
class FilterDecision:
    include: bool
    payload_json: str  # may differ from event.payload_json if redacted


class ProjectionFilter(Protocol):
    def project(self, *, event: EventRow, player_id: str) -> FilterDecision: ...


class PassThroughFilter:
    def project(self, *, event: EventRow, player_id: str) -> FilterDecision:
        return FilterDecision(include=True, payload_json=event.payload_json)
