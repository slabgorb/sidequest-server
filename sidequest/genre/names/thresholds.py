"""Min-pool thresholds for Markov namegen (Story 45-28).

Two thresholds gate corpus size, plus one threshold for stem-collision
rejection. Background: Playtest 2026-04-19 produced "Frandrew Andrew"
— a Markov stem-repetition artifact from undersized corpora. The
guard is the lie-detector that catches a thin corpus *before* it
emits a degenerate name.

Calibration (see ``sprint/context/context-story-45-28.md``):

- ``WARN_BELOW_WORDS = 1000`` is the lookback-2 Markov heuristic floor:
  ~50 phonemic two-grams × 10 alternatives × 2 safety factor.
- ``FAIL_BELOW_WORDS = 200`` is the absolute floor — below this, the
  Markov chain cannot produce non-degenerate output regardless of
  luck. None of the current corpora hit this; the constant exists to
  prevent a future copy-paste regression where someone drops in a
  50-word stub.
- ``STEM_OVERLAP_MIN = 4`` keeps the stem-collision predicate from
  flagging incidental three-letter overlaps (``-and`` in unrelated
  names) while still catching ``andrew`` inside ``frandrew``.
"""

from __future__ import annotations

WARN_BELOW_WORDS = 1000
"""Below this corpus size, emit ``namegen.thin_corpus`` and continue.

Reaching ``WARN_BELOW_WORDS`` is a *quality* signal, not a *blocker*
— the Markov chain still produces output; the warn span tells the GM
panel and the operator that the output came from a thin pool and may
exhibit stem-repetition artifacts.
"""

FAIL_BELOW_WORDS = 200
"""Below this corpus size, raise ``ValueError`` and refuse to generate.

A corpus this small cannot produce coherent Markov output at any
lookback — the chain will regurgitate near-verbatim training words.
We fail loud rather than silently degrading; the narrator subprocess
or CLI caller is responsible for handling the exception (typically by
exiting non-zero and surfacing the failure to the operator).
"""

STEM_OVERLAP_MIN = 4
"""Minimum LCS length below which we ignore stem overlap.

The predicate ``has_stem_collision`` flags only when the longest
common *substring* between two tokens of a generated name reaches
this length AND covers more than half of either token. Below 4 chars,
overlap is incidental and not worth rejecting.
"""


def count_words(text: str) -> int:
    """Whitespace-split word count for a corpus text body.

    Used by both ``build_from_culture`` (to apply the threshold
    guard at corpus-load time) and the audit script (to enumerate
    per-culture corpus sizes). The helper is a neutral counter — it
    does not strip Project Gutenberg headers or otherwise interpret
    content; callers feed it the same text the Markov chain trains on.
    """
    return len(text.split())


__all__ = [
    "FAIL_BELOW_WORDS",
    "STEM_OVERLAP_MIN",
    "WARN_BELOW_WORDS",
    "count_words",
]
