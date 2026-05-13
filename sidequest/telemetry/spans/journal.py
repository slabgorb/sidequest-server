"""Journal spans — JOURNAL_REQUEST replay observability (ADR-100 Seam C, story 50-14)."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_JOURNAL_REPLAY = "journal.replay"
"""Fires when the JOURNAL_REQUEST handler emits a JOURNAL_RESPONSE.

Attributes:
    character_name: name of the seated character whose journal was returned
    entry_count: number of KnownFact entries serialized into the response

Short-duration: the span wraps two ``set_attribute`` calls and no awaited
work. No child spans. Routed flat-only — the GM dashboard surfaces it on
the Subsystems tab component grid alongside other subsystem events.
"""

FLAT_ONLY_SPANS.update({SPAN_JOURNAL_REPLAY})
