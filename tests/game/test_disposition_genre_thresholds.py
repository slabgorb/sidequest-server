"""Genre-configurable disposition→attitude thresholds — Story 50-13 (RED).

Story 50-13 makes the numeric disposition→attitude boundaries
genre-pack-configurable. Today ``Disposition.attitude()`` hardcodes
``> 10 ⇒ friendly`` / ``< -10 ⇒ hostile`` / else neutral
(``disposition.py:78-82``). A pack should be able to widen or narrow the
bands; absent any pack override, behavior must be **byte-identical** to
pre-50-13.

CRITICAL CONSTRAINT (50-12 lesson): the ``Attitude`` enum is the
THREE-tier lowercase wire contract ``friendly`` / ``neutral`` /
``hostile``, locked by ``test_disposition_attitude_enum.py``. This story
moves the numeric BOUNDARIES only. It does NOT add or rename bands. There
is NO five-tier capitalised set. These tests deliberately re-assert the
three-tier shape so a regression that smuggles in extra bands fails here
too.

Architecture note (why module-level config, not an ``attitude(threshold)``
parameter): ``session.apply_world_patch`` reconstructs a fresh
``Disposition(before + delta)`` from a bare int and calls ``.attitude()``
with no arguments; the story forbids revisiting that callsite and the
50-11 span comment explicitly says ``crossed`` is band-identity derived so
"50-13's genre-configurable thresholds can land without revisiting this
callsite." A per-call parameter would force every callsite to thread the
threshold. Therefore the configured thresholds are process-level state in
``sidequest.game.disposition``, set once at pack-load time and read by the
no-argument ``Disposition.attitude()``. These tests pin that surface:

- ``AttitudeThresholds`` — validated model, ``friendly_at`` default +10,
  ``hostile_at`` default -10, strict ``hostile_at < friendly_at``.
- ``DEFAULT_ATTITUDE_THRESHOLDS`` — the ±10 default constant.
- ``configure_attitude_thresholds(t)`` / ``reset_attitude_thresholds()``
  — the loader-facing setter and the isolation/leak-guard reset.
- ``RulesConfig.disposition_thresholds`` — the genre-pack model field
  (None ⇒ defaults). TEA assumption: lives on RulesConfig because
  rules.yaml is the rulebook (EdgeConfig / ResourceDeclaration /
  MetricDef all live there); logged as a design deviation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from sidequest.game.disposition import (
    DEFAULT_ATTITUDE_THRESHOLDS,
    Attitude,
    AttitudeThresholds,
    Disposition,
    configure_attitude_thresholds,
    reset_attitude_thresholds,
)
from sidequest.genre.models.rules import RulesConfig


@pytest.fixture(autouse=True)
def _isolate_threshold_state() -> Iterator[None]:
    """Disposition thresholds are process-level state. Reset before AND
    after every test so (a) tests do not leak ±N into each other and
    (b) the ±10-assuming ``test_disposition_attitude_enum.py`` suite is
    never poisoned if it happens to run after this module in the same
    process. The post-yield reset is the load-bearing one."""
    reset_attitude_thresholds()
    yield
    reset_attitude_thresholds()


# ---------------------------------------------------------------------------
# AC-1 — model field exists, defaults preserve ±10, RRulesConfig default None
# ---------------------------------------------------------------------------


def test_attitude_thresholds_defaults_are_plus_minus_ten() -> None:
    """AC-1: the field defaults to +10 / -10 — the pre-50-13 literals."""
    t = AttitudeThresholds()
    assert t.friendly_at == 10
    assert t.hostile_at == -10


def test_default_attitude_thresholds_constant_is_plus_minus_ten() -> None:
    """The exported default constant must equal a fresh default model so
    the loader can pass ``pack.rules.disposition_thresholds or DEFAULT``
    without divergence between the two default sources."""
    assert DEFAULT_ATTITUDE_THRESHOLDS.friendly_at == 10
    assert DEFAULT_ATTITUDE_THRESHOLDS.hostile_at == -10
    assert AttitudeThresholds() == DEFAULT_ATTITUDE_THRESHOLDS


def test_rules_config_disposition_thresholds_defaults_to_none() -> None:
    """AC-1: an existing pack that never declares the block is unaffected.
    ``None`` (not a populated model) is the 'pack opted out' signal the
    loader maps to ``DEFAULT_ATTITUDE_THRESHOLDS``."""
    assert RulesConfig().disposition_thresholds is None


def test_rules_config_accepts_disposition_thresholds_block() -> None:
    """AC-1/AC-2: a pack may declare the block; it parses into the typed
    model, not a bare dict (so the loader can hand it straight to
    ``configure_attitude_thresholds``)."""
    rc = RulesConfig.model_validate(
        {"disposition_thresholds": {"friendly_at": 5, "hostile_at": -5}}
    )
    assert isinstance(rc.disposition_thresholds, AttitudeThresholds)
    assert rc.disposition_thresholds.friendly_at == 5
    assert rc.disposition_thresholds.hostile_at == -5


# ---------------------------------------------------------------------------
# AC-3 — default / unset behavior is byte-identical to pre-50-13
# ---------------------------------------------------------------------------


# (value, expected) table copied verbatim from the pre-50-13 contract in
# test_disposition_attitude_enum.py. If 50-13 changes ANY default-mode
# cell, that is a regression, not a feature.
_DEFAULT_BOUNDARY_TABLE = [
    (0, Attitude.NEUTRAL),
    (10, Attitude.NEUTRAL),  # strict: 10 is neutral, 11 is friendly
    (11, Attitude.FRIENDLY),
    (-10, Attitude.NEUTRAL),
    (-11, Attitude.HOSTILE),
    (100, Attitude.FRIENDLY),
    (-100, Attitude.HOSTILE),
    (500, Attitude.FRIENDLY),  # clamps to 100 before derivation
    (-500, Attitude.HOSTILE),
]


@pytest.mark.parametrize(("value", "expected"), _DEFAULT_BOUNDARY_TABLE)
def test_unconfigured_attitude_is_byte_identical_to_pre_50_13(
    value: int, expected: Attitude
) -> None:
    """AC-3: with no pack override (state at default), every boundary cell
    matches the pre-50-13 strict ±10 table exactly."""
    assert Disposition(value).attitude() == expected


def test_unconfigured_state_still_three_tier_only() -> None:
    """50-12 guard: the derivation may only ever return one of the three
    locked bands, regardless of configuration. No fourth/fifth tier."""
    seen = {Disposition(v).attitude() for v in range(-100, 101)}
    assert seen == {Attitude.FRIENDLY, Attitude.NEUTRAL, Attitude.HOSTILE}


# ---------------------------------------------------------------------------
# AC-2 / AC-3 — a configured pack reclassifies at the new boundary
# ---------------------------------------------------------------------------


def test_narrowed_bands_reclassify_inward() -> None:
    """AC-2/AC-3: ±5 makes the bands tighter. 6 becomes friendly (was
    neutral under ±10); 5 stays neutral (strict boundary preserved)."""
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=5, hostile_at=-5))
    assert Disposition(5).attitude() == Attitude.NEUTRAL
    assert Disposition(6).attitude() == Attitude.FRIENDLY
    assert Disposition(-5).attitude() == Attitude.NEUTRAL
    assert Disposition(-6).attitude() == Attitude.HOSTILE


def test_widened_bands_reclassify_outward() -> None:
    """AC-2/AC-3: friendly_at=20 widens the neutral band. A disposition of
    15 — friendly under default ±10 — must now read neutral. This is the
    direction a regression that 'only ever tightens' would miss."""
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=20, hostile_at=-20))
    assert Disposition(15).attitude() == Attitude.NEUTRAL
    assert Disposition(21).attitude() == Attitude.FRIENDLY
    assert Disposition(-15).attitude() == Attitude.NEUTRAL
    assert Disposition(-21).attitude() == Attitude.HOSTILE


def test_asymmetric_thresholds_are_independent() -> None:
    """The two bounds are not required to be mirror images. A pack may run
    a generous-friend / slow-to-anger world: friendly_at=3, hostile_at=-30.
    Pins that the derivation reads each bound independently, not a single
    ``abs()`` magnitude."""
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=3, hostile_at=-30))
    assert Disposition(4).attitude() == Attitude.FRIENDLY
    assert Disposition(-29).attitude() == Attitude.NEUTRAL
    assert Disposition(-31).attitude() == Attitude.HOSTILE


# ---------------------------------------------------------------------------
# AC-3 — no module-state leak across pack switches (Python rule #2)
# ---------------------------------------------------------------------------


def test_reset_restores_default_bands_after_a_configured_pack() -> None:
    """AC-3 + python-review #2 (shared mutable state): loading pack A with
    ±5 then switching to a pack that does not declare thresholds must NOT
    leave A's ±5 bleeding into B. ``reset_attitude_thresholds()`` is the
    loader's 'pack opted out' path; after it the table is byte-identical
    to default again. A regression that accumulates module state instead
    of overwriting it would fail here — and would cross-contaminate
    multiplayer sessions on different packs."""
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=5, hostile_at=-5))
    assert Disposition(6).attitude() == Attitude.FRIENDLY  # ±5 active

    reset_attitude_thresholds()

    for value, expected in _DEFAULT_BOUNDARY_TABLE:
        assert Disposition(value).attitude() == expected, (
            f"after reset, Disposition({value}) should be {expected} "
            f"(±10 default) — module state leaked from the ±5 pack"
        )


def test_reconfigure_overwrites_rather_than_accumulates() -> None:
    """Switching directly from ±5 to ±25 (no reset between) must fully
    replace, not merge. 6 was friendly under ±5; under ±25 it is neutral."""
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=5, hostile_at=-5))
    configure_attitude_thresholds(AttitudeThresholds(friendly_at=25, hostile_at=-25))
    assert Disposition(6).attitude() == Attitude.NEUTRAL
    assert Disposition(26).attitude() == Attitude.FRIENDLY


# ---------------------------------------------------------------------------
# AC-4 — malformed thresholds fail loudly (No Silent Fallbacks)
# ---------------------------------------------------------------------------


def test_inverted_thresholds_raise_not_silently_swapped() -> None:
    """AC-4 / SOUL No-Silent-Fallbacks: friendly_at must be strictly
    greater than hostile_at. An inverted pair is a pack authoring error
    and must raise at model-validation time (which is pack-load time),
    never be silently swapped or clamped into a 'working' config."""
    with pytest.raises(ValidationError):
        AttitudeThresholds(friendly_at=-5, hostile_at=5)


def test_equal_thresholds_raise_no_zero_width_neutral_band() -> None:
    """AC-4: friendly_at == hostile_at would collapse the neutral band to
    zero width — almost certainly an authoring mistake. Strict ``<`` keeps
    the three-tier contract meaningful; equal bounds must raise, not
    produce a degenerate two-tier system."""
    with pytest.raises(ValidationError):
        AttitudeThresholds(friendly_at=0, hostile_at=0)


def test_non_integer_threshold_is_rejected_not_coerced() -> None:
    """AC-4: a string in rules.yaml (``friendly_at: "ten"``) must raise,
    not be silently coerced/truncated. Pins strict int typing at the
    parser boundary (python-review #11 input validation)."""
    with pytest.raises(ValidationError):
        AttitudeThresholds.model_validate({"friendly_at": "ten", "hostile_at": -10})


def test_unknown_threshold_key_is_rejected() -> None:
    """python-review #11 / extra=forbid parity with the rest of rules.py
    models: a typo'd key (``frendly_at``) must fail loudly so a pack
    author does not silently get default ±10 while believing they set a
    custom band."""
    with pytest.raises(ValidationError):
        AttitudeThresholds.model_validate(
            {"frendly_at": 5, "hostile_at": -5}  # typo: missing 'i'
        )


# ---------------------------------------------------------------------------
# Public-API / callsite-stability guards (python-review #10, story constraint)
# ---------------------------------------------------------------------------


def test_new_public_symbols_are_exported() -> None:
    """python-review #10 (import hygiene / explicit public API):
    ``disposition.py`` declares ``__all__``. The new public surface must
    be listed so consumers import a stable, intentional API."""
    import sidequest.game.disposition as dispo

    for symbol in (
        "AttitudeThresholds",
        "DEFAULT_ATTITUDE_THRESHOLDS",
        "configure_attitude_thresholds",
        "reset_attitude_thresholds",
    ):
        assert symbol in dispo.__all__, f"{symbol!r} missing from disposition.__all__"


def test_attitude_takes_no_required_arguments() -> None:
    """Story constraint: the SPAN_DISPOSITION_SHIFT callsite in
    ``session.apply_world_patch`` calls ``Disposition(before).attitude()``
    with zero arguments and must NOT be revisited. This guards that 50-13
    did not turn ``attitude()`` into ``attitude(thresholds)`` — which
    would silently break every existing no-arg callsite."""
    import inspect

    sig = inspect.signature(Disposition.attitude)
    required = [
        p
        for name, p in sig.parameters.items()
        if name != "self"
        and p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
    ]
    assert not required, (
        f"Disposition.attitude() grew required parameter(s) {required!r}; "
        f"the session.py callsite calls it with no arguments and must not "
        f"be revisited (50-13 story constraint)"
    )
