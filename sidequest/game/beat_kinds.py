"""Beat kinds + per-kind default delta tables.

Spec: docs/superpowers/specs/2026-04-25-dual-track-momentum-design.md
§"Beat kinds and outcome tiers".

A beat declares one of four ``kind`` values; the kind drives a default delta
table indexed by ``RollOutcome``. A beat can override any per-tier entry
via its ``deltas:`` map. ``resolve_tier_deltas`` merges the kind defaults
with per-beat overrides and returns a flat ``ResolvedDeltas`` consumed by
``_apply_beat``.

All deltas are *signed* and measured against the actor's own/other dials.
``brace`` drains the opponent's dial; that is encoded as a negative
``opponent`` delta so ``opponent.current += deltas.opponent`` is the only
arithmetic the engine needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sidequest.protocol.dice import RollOutcome


class BeatKind(str, Enum):  # noqa: UP042 — matches project convention (see protocol/enums.py)
    """Mechanical contract for a beat.

    - strike: advance own dial / press opponent.
    - brace:  absorb / counter — drains opponent dial.
    - push:   pursue a discrete narrative goal (flee, climb, persuade-out).
    - angle:  set up a scene tag for future leverage.
    """

    strike = "strike"
    brace = "brace"
    push = "push"
    angle = "angle"


@dataclass(frozen=True)
class ResolvedDeltas:
    """Flat deltas resolved for one beat at one outcome tier.

    ``own``/``opponent`` are scalar dial advances. Tag/resolution extras
    are independent flags the engine consults after applying the dials.
    """

    own: int = 0
    opponent: int = 0
    grants_tag: str | None = None
    tag_leverage: int = 0
    grants_fleeting_tag: str | None = None
    tag_backfire: bool = False
    resolution: bool = False


# Per-kind default delta tables. ``b`` is the beat's ``base``; the lambdas
# defer to runtime so we can substitute the live base + target_tag without
# building a fresh table per call.
_DefaultRule = dict[str, Any]  # {own,opponent,grants_tag,...} keyed by str

DEFAULT_DELTAS: dict[BeatKind, dict[RollOutcome, _DefaultRule]] = {
    BeatKind.strike: {
        RollOutcome.CritFail: {},
        RollOutcome.Fail: {},
        RollOutcome.Tie: {"own_expr": "b // 2"},
        RollOutcome.Success: {"own_expr": "b"},
        RollOutcome.CritSuccess: {"own_expr": "b", "grants_fleeting_tag": "Opening"},
    },
    BeatKind.brace: {
        RollOutcome.CritFail: {"opponent": 1},
        RollOutcome.Fail: {},
        RollOutcome.Tie: {"opponent_expr": "-(b // 2)"},
        RollOutcome.Success: {"opponent_expr": "-b"},
        RollOutcome.CritSuccess: {"opponent_expr": "-b", "grants_fleeting_tag": "Counter Stance"},
    },
    BeatKind.push: {
        RollOutcome.CritFail: {"own": -1},
        RollOutcome.Fail: {},
        RollOutcome.Tie: {},
        RollOutcome.Success: {"resolution": True},
        RollOutcome.CritSuccess: {"resolution": True, "grants_fleeting_tag": "Clean Exit"},
    },
    BeatKind.angle: {
        # CritFail: backfire — tag text from target_tag, fleeting, on opposing side.
        RollOutcome.CritFail: {"tag_backfire": True, "grants_fleeting_tag_from_target": True},
        RollOutcome.Fail: {},
        RollOutcome.Tie: {"grants_fleeting_tag_from_target": True},
        RollOutcome.Success: {"grants_tag_from_target": True, "tag_leverage": 1},
        RollOutcome.CritSuccess: {"grants_tag_from_target": True, "tag_leverage": 2},
    },
}


def _eval_expr(expr: str, base: int) -> int:
    """Evaluate a tiny ``b``-only arithmetic expression — closed form, no eval."""
    # Two forms appear in DEFAULT_DELTAS: ``b``, ``b // 2``, ``-b``, ``-(b // 2)``.
    expr = expr.replace(" ", "")
    if expr == "b":
        return base
    if expr == "-b":
        return -base
    if expr == "b//2":
        return base // 2
    if expr == "-(b//2)":
        return -(base // 2)
    raise ValueError(f"unsupported delta expression: {expr!r}")


def resolve_tier_deltas(
    *,
    kind: BeatKind,
    base: int,
    outcome: RollOutcome,
    overrides: dict[RollOutcome, dict[str, Any]] | None,
    target_tag: str | None,
) -> ResolvedDeltas:
    """Merge kind defaults with per-tier overrides into flat ``ResolvedDeltas``.

    Resolution order: kind defaults → per-tier override → engine zeros.

    ``target_tag`` is required for ``angle`` beats (used as the tag text
    on Success/CritSuccess and as the backfire text on CritFail). Other
    kinds may pass ``None``.
    """
    if outcome is RollOutcome.Unknown:
        raise ValueError("RollOutcome.Unknown cannot resolve a beat tier")

    if kind is BeatKind.angle and not target_tag:
        raise ValueError("angle beats require a target_tag")

    rule = dict(DEFAULT_DELTAS[kind][outcome])
    if overrides and outcome in overrides:
        rule.update(overrides[outcome])

    own = int(rule.get("own", 0))
    if "own_expr" in rule:
        own = _eval_expr(rule["own_expr"], base)

    opponent = int(rule.get("opponent", 0))
    if "opponent_expr" in rule:
        opponent = _eval_expr(rule["opponent_expr"], base)

    grants_tag = rule.get("grants_tag")
    grants_fleeting_tag = rule.get("grants_fleeting_tag")
    tag_leverage = int(rule.get("tag_leverage", 0))
    tag_backfire = bool(rule.get("tag_backfire", False))
    resolution = bool(rule.get("resolution", False))

    if rule.get("grants_tag_from_target"):
        grants_tag = target_tag
    if rule.get("grants_fleeting_tag_from_target"):
        grants_fleeting_tag = target_tag

    return ResolvedDeltas(
        own=own,
        opponent=opponent,
        grants_tag=grants_tag,
        tag_leverage=tag_leverage,
        grants_fleeting_tag=grants_fleeting_tag,
        tag_backfire=tag_backfire,
        resolution=resolution,
    )
