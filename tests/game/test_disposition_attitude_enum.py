"""Unit tests for ``Attitude`` enum + ``Disposition`` class — Story 50-10.

Restores the qualitative attitude layer dropped during the 2026-04 Python
port. ADR-020 defines the three-tier mapping (>10 friendly, <-10 hostile,
otherwise neutral). The Rust-era ``Attitude`` + ``Disposition(i32)`` newtype
pattern is being re-introduced as a Python ``Attitude`` enum +
``Disposition`` class with a ``.attitude()`` derivation method.

The string values ``"friendly"`` / ``"neutral"`` / ``"hostile"`` are the
stable wire contract — OTEL spans, GM panel, and narrator NPC
serialization all assume those literals. Story 50-10 must preserve them
exactly so the threshold-crossing fields shipped in 50-11 keep working
without revisiting the SPAN_DISPOSITION_SHIFT route.
"""

from __future__ import annotations

from sidequest.game.disposition import Attitude, Disposition


# ---------------------------------------------------------------------------
# Attitude enum — shape and string values (AC1)
# ---------------------------------------------------------------------------


def test_attitude_enum_has_exactly_three_members() -> None:
    """ADR-020: only three bands. Any fourth member is a doctrine change."""
    members = list(Attitude)
    assert len(members) == 3, f"expected 3 Attitude members, got {len(members)}: {members!r}"


def test_attitude_friendly_value_is_lowercase_string() -> None:
    assert Attitude.FRIENDLY.value == "friendly"


def test_attitude_neutral_value_is_lowercase_string() -> None:
    assert Attitude.NEUTRAL.value == "neutral"


def test_attitude_hostile_value_is_lowercase_string() -> None:
    assert Attitude.HOSTILE.value == "hostile"


def test_attitude_is_str_subclass_for_otel_serialization() -> None:
    """``Attitude`` must subclass ``str`` so it serializes as its value when
    placed into OTEL span attributes and dumped to the watcher event JSON.
    Without this, span_attrs from SPAN_DISPOSITION_SHIFT would emit
    ``{"before_attitude": "Attitude.FRIENDLY"}`` and the GM panel would
    break. Equality test pins the str-subclass contract."""
    assert isinstance(Attitude.FRIENDLY, str)
    assert Attitude.FRIENDLY == "friendly"
    assert Attitude.NEUTRAL == "neutral"
    assert Attitude.HOSTILE == "hostile"


# ---------------------------------------------------------------------------
# Disposition class — value semantics (AC2)
# ---------------------------------------------------------------------------


def test_disposition_default_value_is_zero() -> None:
    assert Disposition().value == 0


def test_disposition_wraps_explicit_int() -> None:
    assert Disposition(15).value == 15


def test_disposition_wraps_negative_int() -> None:
    assert Disposition(-25).value == -25


def test_disposition_clamps_above_positive_bound() -> None:
    """ADR-020: scale is -100..+100. Anything above clamps to 100."""
    assert Disposition(150).value == 100


def test_disposition_clamps_below_negative_bound() -> None:
    assert Disposition(-150).value == -100


def test_disposition_at_positive_bound_is_preserved() -> None:
    assert Disposition(100).value == 100


def test_disposition_at_negative_bound_is_preserved() -> None:
    assert Disposition(-100).value == -100


def test_disposition_int_coercion_returns_clamped_value() -> None:
    """``int(disposition)`` is the integration boundary for legacy code
    (session.py uses ``int(npc.disposition)`` to compute spans). Must
    return the clamped int, never the raw input."""
    assert int(Disposition(50)) == 50
    assert int(Disposition(-200)) == -100
    assert int(Disposition(200)) == 100


# ---------------------------------------------------------------------------
# Disposition.attitude() — strict boundary derivation (AC2/AC4)
# ---------------------------------------------------------------------------


def test_attitude_at_zero_is_neutral() -> None:
    assert Disposition(0).attitude() == Attitude.NEUTRAL


def test_attitude_at_positive_boundary_ten_is_neutral() -> None:
    """ADR-020 strict boundary: 10 is neutral, 11 is friendly."""
    assert Disposition(10).attitude() == Attitude.NEUTRAL


def test_attitude_at_negative_boundary_ten_is_neutral() -> None:
    assert Disposition(-10).attitude() == Attitude.NEUTRAL


def test_attitude_just_above_positive_boundary_is_friendly() -> None:
    assert Disposition(11).attitude() == Attitude.FRIENDLY


def test_attitude_just_below_negative_boundary_is_hostile() -> None:
    assert Disposition(-11).attitude() == Attitude.HOSTILE


def test_attitude_at_max_positive_is_friendly() -> None:
    assert Disposition(100).attitude() == Attitude.FRIENDLY


def test_attitude_at_max_negative_is_hostile() -> None:
    assert Disposition(-100).attitude() == Attitude.HOSTILE


def test_attitude_after_clamp_uses_clamped_value() -> None:
    """A value that exceeds the bounds clamps before attitude derivation,
    not after. Disposition(500).attitude() is Attitude.FRIENDLY (from
    clamped 100), not some out-of-band value."""
    assert Disposition(500).attitude() == Attitude.FRIENDLY
    assert Disposition(-500).attitude() == Attitude.HOSTILE


# ---------------------------------------------------------------------------
# Independence of instances — Python rule #2 (mutable defaults)
# ---------------------------------------------------------------------------


def test_two_disposition_instances_with_defaults_are_independent_objects() -> None:
    """Disposition is a wrapper around mutable state (``value``). Two
    independently constructed instances must not share state. If a future
    refactor caches a singleton zero-disposition or class-level int,
    multiplayer NPCs would inherit each other's affinity shifts."""
    a = Disposition()
    b = Disposition()
    # Same starting value, but separate objects so any future mutation
    # path through ``.value`` or a setter cannot leak across NPCs.
    assert a is not b, "Disposition() must return distinct instances per call"


def test_attitude_string_value_matches_otel_contract_exactly() -> None:
    """The OTEL SPAN_DISPOSITION_SHIFT contract emits ``before_attitude``
    and ``after_attitude`` as the literal strings ``"friendly"`` /
    ``"neutral"`` / ``"hostile"``. The GM panel matches on these
    literals. A casing or whitespace drift here would break the panel
    silently. This locks the wire contract."""
    contract = {Attitude.FRIENDLY.value, Attitude.NEUTRAL.value, Attitude.HOSTILE.value}
    assert contract == {"friendly", "neutral", "hostile"}
