"""Pure fold of the trusted event log into derived StateDelta fields.

Read-only forensic reconstruction. No DB, no I/O, no fabrication: a field
appears in ``FoldResult.derived`` only if some event explicitly carried it.
Mirrors the catch-up fold a reconnecting peer already trusts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sidequest.game.event_log import EventRow

logger = logging.getLogger(__name__)

# Closed StateDelta field set (protocol/models.py:205). Kind-agnostic:
# any event whose payload carries a non-null ``state_delta`` contributes.
STATE_DELTA_FIELDS: tuple[str, ...] = (
    "location",
    "characters",
    "quests",
    "items_gained",
    "encounter_id",
    "party_formation",
    "magic_state",
)


@dataclass(frozen=True)
class DerivedField:
    """One reconstructed StateDelta field and its provenance."""

    value: object
    source_seqs: tuple[int, ...]


@dataclass(frozen=True)
class FoldResult:
    """Outcome of folding an ordered event slice."""

    derived: dict[str, DerivedField] = field(default_factory=dict)
    unparseable_seqs: tuple[int, ...] = ()


def fold_state_deltas(events: list[EventRow]) -> FoldResult:
    """Fold events (any order) into derived StateDelta fields.

    Events are sorted by ``seq`` internally. A payload that fails JSON
    parsing is skipped *loudly* (logged + recorded in
    ``unparseable_seqs``), never silently dropped.
    """
    derived: dict[str, DerivedField] = {}
    unparseable: list[int] = []
    for ev in sorted(events, key=lambda e: e.seq):
        try:
            payload = json.loads(ev.payload_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("forensic_fold.unparseable_payload seq=%s", ev.seq)
            unparseable.append(ev.seq)
            continue
        if not isinstance(payload, dict):
            logger.warning("forensic_fold.non_dict_payload seq=%s", ev.seq)
            unparseable.append(ev.seq)
            continue
        sd = payload.get("state_delta")
        if not isinstance(sd, dict):
            continue
        for fname in STATE_DELTA_FIELDS:
            fval = sd.get(fname)
            if fval is None:
                continue
            prev = derived.get(fname)
            seqs = (*prev.source_seqs, ev.seq) if prev else (ev.seq,)
            derived[fname] = DerivedField(value=fval, source_seqs=seqs)
    return FoldResult(derived=derived, unparseable_seqs=tuple(unparseable))
