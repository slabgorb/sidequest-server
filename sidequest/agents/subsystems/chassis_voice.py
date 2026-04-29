"""Resolve a chassis's current address-form for a named character.

The narrator prompt builder calls this when generating chassis-as-speaker
dialogue. Asymmetric bond: the chassis's *own* tier (chassis_to_character)
governs how it addresses the character, regardless of how the character
feels about the chassis.

If the chassis has no voice block or no bond ledger entry for the
character, return the default "Pilot" form. This is a true fallback (not
a silent-fallback violation) — the chassis is just unfamiliar.
"""
from __future__ import annotations

from typing import Protocol

from sidequest.game.chassis import ChassisInstance


class _CharacterLike(Protocol):
    id: str
    first_name: str
    last_name: str
    nickname: str | None


_DEFAULT_FORM = "Pilot"


def resolve_chassis_name_form(
    chassis: ChassisInstance,
    character: _CharacterLike,
) -> str:
    """Return the chassis's current address-form for the character."""
    if chassis.voice is None:
        return _DEFAULT_FORM

    entry = chassis.bond_for(character.id)
    if entry is None:
        return _DEFAULT_FORM

    template = chassis.voice.name_forms_by_bond_tier.get(
        entry.bond_tier_chassis, _DEFAULT_FORM,
    )

    # Per spec §7 open question: {nickname} with no nickname source
    # falls back to the trusted-tier form so prose stays coherent.
    if "{nickname}" in template and not character.nickname:
        template = chassis.voice.name_forms_by_bond_tier.get(
            "trusted", _DEFAULT_FORM,
        )

    return template.format(
        first_name=character.first_name,
        last_name=character.last_name,
        nickname=character.nickname or character.first_name,
    )
