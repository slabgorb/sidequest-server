"""Unit tests for namegen min-pool guard + stem-collision predicate (Story 45-28).

These tests pin the *contract* of the threshold module and the
stem-collision predicate in isolation. They are the unit lane —
``test_namegen_wiring.py`` is the wire-first lane that proves the
predicate and the guards are actually invoked from
``build_from_culture`` and ``cli/namegen/namegen.py:generate_npc``.

Constants (per ``sprint/context/context-story-45-28.md``):

- ``WARN_BELOW_WORDS = 1000`` — fire ``namegen.thin_corpus`` and continue.
  All three Aureate Span source corpora (latin/polynesian/georgian) hit
  this today; that's why this story exists.
- ``FAIL_BELOW_WORDS = 200`` — raise ``ValueError`` and emit
  ``namegen.fail_loud``. Floor under which the Markov chain cannot
  produce non-degenerate output regardless of luck.
- ``STEM_OVERLAP_MIN = 4`` — minimum LCS length under which we ignore
  overlap. "Frandrew Andrew" → LCS ``andrew`` length 6, well over 4.

Stem-collision predicate (per architect context):

  Strip whitespace, case-fold each space-separated token, compute the
  longest common *substring* of length ≥ ``STEM_OVERLAP_MIN``. If the
  substring spans more than half of *either* token, reject as a
  collision. The predicate operates within a single generated name's
  tokens — never across separate names (Vaal-Kesh / Vaal-Tor across
  two names is intentional cultural coherence, not a collision).
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Threshold constants — module + values
# ---------------------------------------------------------------------------


def test_thresholds_module_imports() -> None:
    """``sidequest.genre.names.thresholds`` exists and is importable.

    The architect's context (``Two thresholds, not one``) prescribes a
    dedicated module so the constants are a single edit-point. Living
    them inside ``generator.py`` would force the audit script to import
    the full Markov machinery just to read two ints.
    """
    import sidequest.genre.names.thresholds  # noqa: F401


def test_warn_below_words_value() -> None:
    """``WARN_BELOW_WORDS`` is exactly ``1000`` (architect calibration).

    1000 is the lookback-2 Markov heuristic floor — ~50 phonemic
    two-grams × 10 alternatives × 2 safety factor — calibrated against
    the on-disk distribution where the three thin Aureate Span corpora
    (latin/polynesian/georgian, ~300-340 words each) are the only
    cultures that trip the warn span today.
    """
    from sidequest.genre.names.thresholds import WARN_BELOW_WORDS

    assert WARN_BELOW_WORDS == 1000, (
        "Threshold drifted from architect calibration; if you must lower "
        "it, update sprint/context/context-story-45-28.md (Two thresholds, "
        "not one) and the audit_namegen_corpora exit-code expectations."
    )


def test_fail_below_words_value() -> None:
    """``FAIL_BELOW_WORDS`` is exactly ``200`` (architect calibration).

    200 is the hard floor — no current corpus hits it; the constant
    exists to prevent a future copy-paste regression where someone drops
    in a 50-word stub and silently degrades the namegen.
    """
    from sidequest.genre.names.thresholds import FAIL_BELOW_WORDS

    assert FAIL_BELOW_WORDS == 200, (
        "Threshold drifted; FAIL_BELOW_WORDS guards the Markov chain's "
        "absolute lower bound — see architect context for derivation."
    )


def test_fail_below_is_strictly_below_warn() -> None:
    """``FAIL_BELOW_WORDS < WARN_BELOW_WORDS`` — bands cannot overlap.

    An equal or inverted ordering would make the warn span unreachable
    (every thin corpus would FAIL first) or the fail span unreachable
    (every fail-eligible corpus would WARN and continue).
    """
    from sidequest.genre.names.thresholds import (
        FAIL_BELOW_WORDS,
        WARN_BELOW_WORDS,
    )

    assert FAIL_BELOW_WORDS < WARN_BELOW_WORDS


def test_stem_overlap_min_value() -> None:
    """``STEM_OVERLAP_MIN`` is exactly ``4`` (architect calibration).

    Below 4 the predicate flags coincidental three-letter overlaps
    (``-and`` in unrelated names); at 4+ it catches stem-repetition
    artifacts like ``Andrew`` inside ``Frandrew``.
    """
    from sidequest.genre.names.thresholds import STEM_OVERLAP_MIN

    assert STEM_OVERLAP_MIN == 4


# ---------------------------------------------------------------------------
# Stem-collision predicate — the load-bearing rejector
# ---------------------------------------------------------------------------


def test_stem_collision_flags_frandrew_andrew() -> None:
    """The original Playtest 3 artifact is rejected.

    "Frandrew Andrew" has tokens ``frandrew`` (8 chars) and ``andrew``
    (6 chars); LCS is ``andrew`` (6 chars), spanning 100% of token 2
    and 75% of token 1 — over the 50% half-token bar.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Frandrew Andrew") is True


def test_stem_collision_passes_distinct_tokens() -> None:
    """Two genuinely distinct tokens with no LCS ≥ 4 pass through.

    "Solenne Veradaine" has no 4-char common substring; the predicate
    must NOT flag it — this is exactly the kind of name the Aureate
    Span culture should keep producing.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Solenne Veradaine") is False


def test_stem_collision_passes_short_overlap() -> None:
    """Three-character overlaps fall below ``STEM_OVERLAP_MIN`` and pass.

    ``Mivaan Tor`` shares ``a`` only — the predicate must not flag
    incidental letter overlap as collision.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Mivaan Tor") is False


def test_stem_collision_is_case_insensitive() -> None:
    """Case-folding before the LCS comparison.

    Without folding, ``Frandrew andrew`` (lowercase second token) would
    pass the predicate even though it exhibits the same artifact.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Frandrew andrew") is True
    assert has_stem_collision("FRANDREW Andrew") is True


def test_stem_collision_passes_single_token_names() -> None:
    """Single-token names cannot collide with themselves.

    Vaal-Kesh given names (``Mivaan``) often emit as a single token in
    the trade-name pattern. The predicate is defined over ≥ 2 tokens —
    a single token must pass.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Mivaan") is False


def test_stem_collision_ignores_small_words() -> None:
    """Connector tokens (``de``, ``of``, ``the``) do not anchor LCS.

    "Solenne de Veradaine" tokenises to three tokens; the ``de`` token
    is too short to participate in a 4-char overlap. The predicate must
    not mistake the connector for a collision.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Solenne de Veradaine") is False


def test_stem_collision_flags_long_substring_collision() -> None:
    """Mid-token overlap with ≥ 4 chars and >50% coverage flags.

    ``Andrewson Andrew`` has LCS ``andrew`` (6 chars), 100% of token 2,
    ~67% of token 1 — flagged.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Andrewson Andrew") is True


def test_stem_collision_flags_within_long_token_pair() -> None:
    """Overlap need not anchor at token edges; it's the longest *substring*.

    ``Pranderil Anderil`` shares ``anderil`` (7 chars) — well over the
    threshold and over 50% of either token. Flagged.
    """
    from sidequest.genre.names.generator import has_stem_collision

    assert has_stem_collision("Pranderil Anderil") is True


def test_stem_collision_passes_low_coverage_overlap() -> None:
    """An LCS of length 4 that covers ≤ 50% of *both* tokens passes.

    First case: short tokens, high coverage — flag. ``Veradaine
    Veradaire`` share the 7-char prefix ``veradai``; 7/9 ≈ 78% of each
    token is over the 50% bar.

    Second case: long tokens with a deliberately-isolated 4-char
    overlap — pass. ``Solenneabcd Carensabcd`` share ``abcd`` (4
    chars) only; 4/11 ≈ 36% and 4/10 = 40% on each token, both below
    the 50% bar. The predicate operates on coverage, not bare LCS
    length, so culturally-coherent stem reuse in long tokens does not
    over-fire.
    """
    from sidequest.genre.names.generator import has_stem_collision

    # First case: short tokens, high coverage — flag.
    assert has_stem_collision("Veradaine Veradaire") is True

    # Second case: long tokens, low coverage — pass.
    assert has_stem_collision("Solenneabcd Carensabcd") is False


# ---------------------------------------------------------------------------
# Helper sanity — _count_words behaves as expected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("hello", 1),
        ("hello world", 2),
        ("hello\nworld", 2),
        ("hello   world\n\nfoo", 3),
        # Project Gutenberg headers count as words too — the audit script
        # is responsible for running the strip; the helper itself is a
        # neutral counter.
        ("*** START OF THIS PROJECT GUTENBERG EBOOK ***\nhello", 9),
    ],
)
def test_count_words(text: str, expected: int) -> None:
    """``_count_words`` is whitespace-split on the raw text.

    The audit script and ``build_from_culture`` both feed this helper
    *post-PG-header-strip* (or pre-strip, depending on context); the
    helper itself does not interpret content. Tests pin the simple
    behavior so future "smart" rewrites that double-count or skip lines
    fail loud.
    """
    from sidequest.genre.names.thresholds import count_words

    assert count_words(text) == expected
