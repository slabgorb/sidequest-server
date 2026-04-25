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


# ---------------------------------------------------------------------------
# apply_beat — shared between narrator and dice-throw paths
# ---------------------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402

from sidequest.game.encounter import (  # noqa: E402
    EncounterActor,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.game.encounter_tag import EncounterTag  # noqa: E402
from sidequest.telemetry.spans import (  # noqa: E402
    encounter_metric_advance_span,
    encounter_tag_backfire_span,
    encounter_tag_created_span,
)
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish  # noqa: E402


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one ``apply_beat`` invocation.

    ``skipped_reason`` is non-None when the beat was dropped — the encounter
    state is unchanged. ``resolved`` is True when this beat caused the
    encounter to flip ``resolved=True``.
    """

    deltas: ResolvedDeltas | None
    resolved: bool
    skipped_reason: str | None = None


def _phase_for_beat(beat: int) -> EncounterPhase:
    ladder = {
        0: EncounterPhase.Setup,
        1: EncounterPhase.Opening,
        2: EncounterPhase.Escalation,
        3: EncounterPhase.Escalation,
        4: EncounterPhase.Escalation,
    }
    return ladder.get(beat, EncounterPhase.Climax)


def _opposite_side_first_actor(
    enc: StructuredEncounter, side: str,
) -> str | None:
    other = "opponent" if side == "player" else "player"
    for a in enc.actors:
        if a.side == other and not a.withdrawn:
            return a.name
    return None


def _normalize_overrides(
    raw: dict[str, dict] | None,
) -> dict[RollOutcome, dict] | None:
    if raw is None:
        return None
    mapping = {
        "crit_fail": RollOutcome.CritFail,
        "fail": RollOutcome.Fail,
        "tie": RollOutcome.Tie,
        "success": RollOutcome.Success,
        "crit_success": RollOutcome.CritSuccess,
    }
    return {mapping[k]: v for k, v in raw.items()}


def apply_beat(
    enc: StructuredEncounter,
    actor: EncounterActor,
    beat: Any,  # BeatDef — typed as Any to dodge circular import
    outcome: RollOutcome,
    *,
    turn: int = 0,
) -> ApplyResult:
    """Apply one beat at one outcome tier to the encounter.

    Routes the deltas to the actor's side, processes tag/resolution extras,
    advances ``enc.beat`` and ``structured_phase``, and detects threshold
    crossings. Emits ``encounter.metric_advance``, ``encounter.tag_created``,
    and (on angle CritFail) ``encounter.tag_backfire`` spans.

    Skips with a structured reason when the actor is neutral, withdrawn,
    or the encounter is already resolved.
    """
    if enc.resolved:
        return ApplyResult(deltas=None, resolved=False, skipped_reason="encounter_resolved")
    if actor.side == "neutral":
        return ApplyResult(deltas=None, resolved=False, skipped_reason="neutral_actor")
    if actor.withdrawn:
        return ApplyResult(deltas=None, resolved=False, skipped_reason="withdrawn_actor")

    overrides = _normalize_overrides(getattr(beat, "deltas", None))
    deltas = resolve_tier_deltas(
        kind=beat.kind,
        base=getattr(beat, "base", 1),
        outcome=outcome,
        overrides=overrides,
        target_tag=getattr(beat, "target_tag", None),
    )

    own_metric = enc.player_metric if actor.side == "player" else enc.opponent_metric
    other_metric = enc.opponent_metric if actor.side == "player" else enc.player_metric

    if deltas.own != 0:
        before = own_metric.current
        own_metric.current = max(0, own_metric.current + deltas.own)
        with encounter_metric_advance_span(
            side=actor.side, delta_kind="own", delta=deltas.own,
            before=before, after=own_metric.current,
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "metric_advance",
                "side": actor.side,
                "delta_kind": "own",
                "delta": deltas.own,
                "before": before,
                "after": own_metric.current,
            },
            component="encounter",
        )

    if deltas.opponent != 0:
        before = other_metric.current
        # Opponent dial: ``brace`` emits a negative delta; ascending dials
        # are clamped at 0.
        other_metric.current = max(0, other_metric.current + deltas.opponent)
        cross_side = "opponent" if actor.side == "player" else "player"
        with encounter_metric_advance_span(
            side=cross_side,
            delta_kind="cross", delta=deltas.opponent,
            before=before, after=other_metric.current,
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "metric_advance",
                "side": cross_side,
                "delta_kind": "cross",
                "delta": deltas.opponent,
                "before": before,
                "after": other_metric.current,
            },
            component="encounter",
        )

    if deltas.tag_backfire:
        # Angle CritFail: tag goes onto the opposing side, fleeting.
        target_actor_name = _opposite_side_first_actor(enc, actor.side)
        tag = EncounterTag(
            text=getattr(beat, "target_tag", "Backfire"),
            created_by=actor.name,
            target=target_actor_name,
            leverage=1,
            fleeting=True,
            created_turn=turn,
        )
        enc.tags.append(tag)
        with encounter_tag_backfire_span(
            tag_text=tag.text, created_by=actor.name,
            target=target_actor_name or "", triggering_beat=beat.id,
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "tag_backfire",
                "tag_text": tag.text,
                "created_by": actor.name,
                "target": target_actor_name or "",
                "triggering_beat": beat.id,
                "fleeting": True,
                "leverage": 1,
            },
            component="encounter",
        )
    elif deltas.grants_tag:
        tag = EncounterTag(
            text=deltas.grants_tag,
            created_by=actor.name,
            target=_opposite_side_first_actor(enc, actor.side),
            leverage=deltas.tag_leverage or 1,
            fleeting=False,
            created_turn=turn,
        )
        enc.tags.append(tag)
        with encounter_tag_created_span(
            tag_text=tag.text, created_by=actor.name,
            target=tag.target, leverage=tag.leverage, fleeting=False,
            created_via="angle_beat",
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "tag_created",
                "tag_text": tag.text,
                "created_by": actor.name,
                "target": tag.target or "",
                "leverage": tag.leverage,
                "fleeting": False,
                "created_via": "angle_beat",
            },
            component="encounter",
        )

    if deltas.grants_fleeting_tag and not deltas.tag_backfire:
        tag = EncounterTag(
            text=deltas.grants_fleeting_tag,
            created_by=actor.name,
            target=_opposite_side_first_actor(enc, actor.side),
            leverage=1,
            fleeting=True,
            created_turn=turn,
        )
        enc.tags.append(tag)
        with encounter_tag_created_span(
            tag_text=tag.text, created_by=actor.name,
            target=tag.target, leverage=1, fleeting=True,
            created_via="extras",
        ):
            pass
        _watcher_publish(
            "state_transition",
            {
                "field": "encounter",
                "op": "tag_created",
                "tag_text": tag.text,
                "created_by": actor.name,
                "target": tag.target or "",
                "leverage": 1,
                "fleeting": True,
                "created_via": "extras",
            },
            component="encounter",
        )

    enc.beat += 1
    enc.structured_phase = _phase_for_beat(enc.beat)

    resolved = False

    # Player threshold first, then opponent — sealed-letter order via
    # ADR-036 already places player beats first in the iteration; this
    # second-level tie-break is "first crossing wins".
    if enc.player_metric.current >= enc.player_metric.threshold:
        enc.resolved = True
        enc.outcome = "player_victory"
        enc.structured_phase = EncounterPhase.Resolution
        resolved = True
    elif enc.opponent_metric.current >= enc.opponent_metric.threshold:
        enc.resolved = True
        enc.outcome = "opponent_victory"
        enc.structured_phase = EncounterPhase.Resolution
        resolved = True
    elif deltas.resolution or getattr(beat, "resolution", False):
        enc.resolved = True
        enc.outcome = f"resolution_beat:{beat.id}"
        enc.structured_phase = EncounterPhase.Resolution
        resolved = True

    return ApplyResult(deltas=deltas, resolved=resolved, skipped_reason=None)
