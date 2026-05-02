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

FLAT_ONLY_SPANS.update(
    {
        SPAN_RIG_BOND_EVENT,
        SPAN_RIG_VOICE_REGISTER_CHANGE,
        SPAN_RIG_CONFRONTATION_OUTCOME,
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
