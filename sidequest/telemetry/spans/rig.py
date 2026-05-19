"""rig.* OTEL span constants + emitters for the chassis framework.

Slice scope: three flat-only emitters. The taxonomy declares ten more;
they ship with their producing subsystems (subsystem install/remove with
hardpoints; damage_resolution with dogfight; ancillary_loss with
ancillary support; etc.).

Emitters fire on state-mutation points and have no inner work — `pass`
inside the `Span.open` context is intentional.

Per the magic.py precedent, None-valued attributes are coerced to "" so
OTEL doesn't drop them.
"""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS
from .span import Span

SPAN_RIG_BOND_EVENT = "rig.bond_event"
SPAN_RIG_VOICE_REGISTER_CHANGE = "rig.voice_register_change"
SPAN_RIG_CONFRONTATION_OUTCOME = "rig.confrontation_outcome"
SPAN_ROOM_ENTRY_SKIPPED = "room.entry_skipped"
SPAN_ROOM_ENTRY_EVALUATED = "room.entry_evaluated"

# Story 53-1: RigComposurePool emits these three on construct / delta /
# zero-crossing. The crash handler (story 53-3) subscribes to
# rig_pool.zero_crossing to fire injury tags + Edge loss + dismount.
SPAN_RIG_POOL_CREATED = "rig_pool.created"
SPAN_RIG_POOL_DELTA = "rig_pool.delta"
SPAN_RIG_POOL_ZERO_CROSSING = "rig_pool.zero_crossing"

# Story 53-3: rig crash handler emits crash_event when Composure→0
# triggers the injury + Edge -1 + dismount consequences. Attrs include
# character_id, chassis_id, location, attacker per road_warrior rules.yaml
# rig_composure_spec.
SPAN_RIG_POOL_CRASH_EVENT = "rig_pool.crash_event"

FLAT_ONLY_SPANS.update(
    {
        SPAN_RIG_BOND_EVENT,
        SPAN_RIG_VOICE_REGISTER_CHANGE,
        SPAN_RIG_CONFRONTATION_OUTCOME,
        SPAN_ROOM_ENTRY_SKIPPED,
        SPAN_ROOM_ENTRY_EVALUATED,
        SPAN_RIG_POOL_CREATED,
        SPAN_RIG_POOL_DELTA,
        SPAN_RIG_POOL_ZERO_CROSSING,
        SPAN_RIG_POOL_CRASH_EVENT,
    }
)


def emit_rig_bond_event(
    *,
    chassis_id: str,
    actor_id: str,
    side: str,
    delta_character: float,
    delta_chassis: float,
    tier_character_before: str,
    tier_character_after: str,
    tier_chassis_before: str,
    tier_chassis_after: str,
    confrontation_id: str | None,
    register: str,
) -> None:
    with Span.open(
        SPAN_RIG_BOND_EVENT,
        attrs={
            "chassis_id": chassis_id,
            "actor_id": actor_id,
            "side": side,
            "delta_character": delta_character,
            "delta_chassis": delta_chassis,
            "tier_character_before": tier_character_before,
            "tier_character_after": tier_character_after,
            "tier_chassis_before": tier_chassis_before,
            "tier_chassis_after": tier_chassis_after,
            "confrontation_id": confrontation_id or "",
            "register": register,
        },
    ):
        pass


def emit_rig_voice_register_change(
    *,
    chassis_id: str,
    actor_id: str,
    register_before: str,
    register_after: str,
    triggering_event: str,
) -> None:
    with Span.open(
        SPAN_RIG_VOICE_REGISTER_CHANGE,
        attrs={
            "chassis_id": chassis_id,
            "actor_id": actor_id,
            "register_before": register_before,
            "register_after": register_after,
            "triggering_event": triggering_event,
        },
    ):
        pass


def emit_room_entry_skipped(
    *,
    reason: str,
    room_id: str,
    actor_id: str,
) -> None:
    """Story 47-6: every silent return path of process_room_entry must
    emit this span so the GM dashboard can see why eligibility wasn't
    evaluated. ``reason`` is currently one of: ``chassis_not_found``,
    ``not_chassis_room``, ``no_bond_for_actor``. Story 47-7 will add
    ``no_magic_state`` when magic_state becomes load-bearing for
    confrontation outputs."""
    with Span.open(
        SPAN_ROOM_ENTRY_SKIPPED,
        attrs={
            "reason": reason,
            "room_id": room_id,
            "actor_id": actor_id,
        },
    ):
        pass


def emit_room_entry_evaluated(
    *,
    chassis_id: str,
    room_local_id: str,
    eligible_count: int,
    fired_count: int,
) -> None:
    """Story 47-6: emitted after process_room_entry resolves a chassis
    room and runs the auto-fire eligibility evaluator. ``fired_count``
    < ``eligible_count`` when cooldown blocks dispatch, so the GM
    panel can distinguish "nothing matched" from "matched but on
    cooldown"."""
    with Span.open(
        SPAN_ROOM_ENTRY_EVALUATED,
        attrs={
            "chassis_id": chassis_id,
            "room_local_id": room_local_id,
            "eligible_count": eligible_count,
            "fired_count": fired_count,
        },
    ):
        pass


def emit_rig_confrontation_outcome(
    *,
    chassis_id: str,
    confrontation_id: str,
    register: str,
    branch: str,
    outputs: list[str],
) -> None:
    with Span.open(
        SPAN_RIG_CONFRONTATION_OUTCOME,
        attrs={
            "chassis_id": chassis_id,
            "confrontation_id": confrontation_id,
            "register": register,
            "branch": branch,
            "outputs": list(outputs),
        },
    ):
        pass
