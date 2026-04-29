"""Namegen subsystem spans (Story 45-28).

Three spans gate the Markov namegen subsystem on the lie-detector
side of CLAUDE.md's OTEL Observability Principle:

- ``namegen.thin_corpus`` — fires when ``build_from_culture`` loads
  a corpus below ``WARN_BELOW_WORDS``. Generation continues; the
  span is the GM panel's signal that the chain is at risk of stem
  repetition. Sebastien (mechanical-first player, watches the GM
  panel) needs this confirmation that the audit ran *and* what it
  found, not just that names appeared.

- ``namegen.fail_loud`` — fires from two paths: corpus load below
  ``FAIL_BELOW_WORDS`` (``reason="below_floor"``, raises after
  emitting), and ``generate_npc`` rejection-loop exhaustion
  (``reason="stem_collision_exhausted"``). Both are unrecoverable;
  the span fires *before* the failure-handling code path so the
  GM panel sees the failure even if the caller swallows the exception.

- ``namegen.stem_collision`` — fires once per rejected attempt in the
  ``generate_npc`` 10-attempt loop. Carries the candidate name and
  the stems that collided, so the GM panel can render a chronological
  rejection trail and the operator can see *why* the generator
  retried.

Background: Playtest 2026-04-19 produced "Frandrew Andrew" — a
classic stem-repetition Markov artifact from undersized
``aureate_span`` source corpora. These three spans are the verification
that the fix is actually firing.
"""

from __future__ import annotations

from ._core import SPAN_ROUTES, SpanRoute

SPAN_NAMEGEN_THIN_CORPUS = "namegen.thin_corpus"
SPAN_ROUTES[SPAN_NAMEGEN_THIN_CORPUS] = SpanRoute(
    event_type="state_transition",
    component="namegen",
    extract=lambda span: {
        "field": "corpus",
        "op": "thin_corpus",
        "corpus_name": (span.attributes or {}).get("corpus_name", ""),
        "word_count": (span.attributes or {}).get("word_count", 0),
        "culture": (span.attributes or {}).get("culture", ""),
        "slot_name": (span.attributes or {}).get("slot_name", ""),
        "threshold": (span.attributes or {}).get("threshold", 0),
    },
)

SPAN_NAMEGEN_FAIL_LOUD = "namegen.fail_loud"
SPAN_ROUTES[SPAN_NAMEGEN_FAIL_LOUD] = SpanRoute(
    event_type="state_transition",
    component="namegen",
    extract=lambda span: {
        "field": "corpus",
        "op": "fail_loud",
        # Optional for the stem-collision-exhausted variant — that path
        # doesn't have a single corpus to point at; default empty.
        "corpus_name": (span.attributes or {}).get("corpus_name", ""),
        "word_count": (span.attributes or {}).get("word_count", 0),
        "culture": (span.attributes or {}).get("culture", ""),
        "slot_name": (span.attributes or {}).get("slot_name", ""),
        "reason": (span.attributes or {}).get("reason", ""),
    },
)

SPAN_NAMEGEN_STEM_COLLISION = "namegen.stem_collision"
SPAN_ROUTES[SPAN_NAMEGEN_STEM_COLLISION] = SpanRoute(
    event_type="state_transition",
    component="namegen",
    extract=lambda span: {
        "field": "corpus",
        "op": "stem_collision",
        "culture": (span.attributes or {}).get("culture", ""),
        "candidate": (span.attributes or {}).get("candidate", ""),
        "prefix_stem": (span.attributes or {}).get("prefix_stem", ""),
        "suffix_stem": (span.attributes or {}).get("suffix_stem", ""),
        "attempt_index": (span.attributes or {}).get("attempt_index", 0),
    },
)
