"""Pure fold of the trusted event log into the derived KnownFacts ledger.

Read-only forensic reconstruction. No DB, no I/O, no fabrication: a fact
appears in ``FoldResult.derived`` only if some event explicitly carried it
as a footnote.

**Why footnotes, not ``state_delta``** (verified against every real save
2026-05-18): recorded ``events`` rows are only ``NARRATION`` /
``SCRAPBOOK_ENTRY``; NARRATION payloads are ``{text, footnotes,
_visibility}`` and carry **no** ``state_delta`` key. Authoritative
mechanical state lives in the single ``game_state`` snapshot (the "Final
stored snapshot" panel); the per-player rendered view lives in
``projection_cache`` (the "projection lens" panel). The only real
*per-turn, derived-not-stored* signal in the event log is the footnote /
KnownFacts stream (ADR-100) — the narrator's accumulating working memory,
which is precisely how a long game stays coherent. Folding it by
``fact_id`` answers "what had the narrator established by round N, and on
which turns?" — the genuine autopsy distinct from the stored snapshot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sidequest.game.event_log import EventRow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DerivedField:
    """One reconstructed KnownFact and its provenance.

    ``value`` is the latest (highest-seq) ``{summary, category}`` for the
    fact; ``source_seqs`` is every event seq that asserted or restated it,
    in seq order.
    """

    value: object
    source_seqs: tuple[int, ...]


@dataclass(frozen=True)
class FoldResult:
    """Outcome of folding an ordered event slice. ``derived`` is keyed by
    ``fact_id`` (the fact's stable identity)."""

    derived: dict[str, DerivedField] = field(default_factory=dict)
    unparseable_seqs: tuple[int, ...] = ()


def fold_known_facts(events: list[EventRow]) -> FoldResult:
    """Fold events (any order) into the derived KnownFacts ledger.

    Events are sorted by ``seq`` internally. Folding rules:

    - A payload that fails JSON parsing, or parses to a non-dict, is
      skipped *loudly* (logged + recorded in ``unparseable_seqs``), never
      silently dropped.
    - An event with no ``footnotes`` list (SCRAPBOOK_ENTRY, footnote-less
      NARRATION) contributes nothing — that is not an error.
    - A footnote with no string ``fact_id`` has no stable identity; it is
      skipped *loudly* (``forensic_fold.malformed_footnote``) but its
      well-formed siblings in the same event still fold (No-Silent-
      Fallbacks: log, don't poison the whole event).
    - Repeated ``fact_id``: ``source_seqs`` accumulates every seq in order;
      ``value`` is the highest-seq assertion's ``{summary, category}``.
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
        footnotes = payload.get("footnotes")
        if not isinstance(footnotes, list):
            continue
        for fn in footnotes:
            if not isinstance(fn, dict):
                logger.warning("forensic_fold.malformed_footnote seq=%s", ev.seq)
                continue
            fact_id = fn.get("fact_id")
            if not isinstance(fact_id, str) or not fact_id:
                logger.warning("forensic_fold.malformed_footnote seq=%s", ev.seq)
                continue
            prev = derived.get(fact_id)
            seqs = (*prev.source_seqs, ev.seq) if prev else (ev.seq,)
            derived[fact_id] = DerivedField(
                value={"summary": fn.get("summary"), "category": fn.get("category")},
                source_seqs=seqs,
            )
    return FoldResult(derived=derived, unparseable_seqs=tuple(unparseable))
