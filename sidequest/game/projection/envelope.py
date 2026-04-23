"""MessageEnvelope — what the ProjectionFilter judges.

Superset of EventRow. Today's callers (live fan-out, reconnect replay) only
construct envelopes from EventLog events; tomorrow's caller may construct
them for non-EventLog outbound messages (see spec §Out of Scope — all-outbound-
message coverage).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageEnvelope:
    kind: str
    payload_json: str
    origin_seq: int | None
