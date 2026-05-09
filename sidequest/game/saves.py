"""B/X B26 saving-throw resolver.

Implements ``resolve_save`` and ``apply_spell_effect`` per the design spec at
``docs/superpowers/specs/2026-05-09-cnc-bx-saving-throws-design.md``.

Key design facts:
- Save targets come from ``ClassDef.saving_throws`` (a ``SavingThrowsTable``).
- The WIS (or other ability) modifier is added to the raw d20 roll.
- ``dragon_breath`` (and any future category wired with ``ability=None``)
  ignores the ability modifier entirely — raw roll vs. target.
- Hard-fail-loud: missing class key raises ``KeyError``; missing save table
  raises ``ValueError``. No silent fallbacks (CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sidequest.game.encounter import EncounterActor
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import SaveCategory
from sidequest.protocol.dice import RollOutcome

# Margin above target needed to escalate a plain Success into a CritSuccess.
_DECISIVE_MARGIN = 10


def _ability_modifier(score: int) -> int:
    """D&D-style ability modifier: floor((score - 10) / 2)."""
    return (score - 10) // 2


def _defender_score(defender: EncounterActor, ability: str) -> int:
    """Extract the raw ability score from defender's per_actor_state.

    Hard-fails with ``ValueError`` if ``stats`` block is missing or the
    specific ability is absent — no silent fallbacks.
    """
    stats: dict[str, int] | None = defender.per_actor_state.get("stats")
    if stats is None:
        raise ValueError(f"actor '{defender.name}' has no 'stats' block in per_actor_state")
    # Case-insensitive lookup: try exact key first, then uppercase.
    if ability in stats:
        return stats[ability]
    upper = ability.upper()
    if upper in stats:
        return stats[upper]
    raise ValueError(
        f"actor '{defender.name}' stats block has no ability '{ability}' "
        f"(available: {sorted(stats)})"
    )


# Design note: the original spec sketched calling ``resolve_opposed_check``
# with a synthetic opponent for tier classification.  The cleaner path is the
# 7-line ``_classify_save_tier`` below — saves are not opposed checks; they
# compare a single modified roll against a fixed target number.  The
# ``fixed_opponent_roll`` kwarg shipped in Task 5 remains available for future
# trap/environment saves that need it, but this module does not use it.
def _classify_save_tier(*, roll: int, total: int, target: int) -> RollOutcome:
    """Map a d20 roll + modifier vs. target to a ``RollOutcome`` tier.

    Priority order (nat-1/nat-20 override modifiers; decisive-margin
    escalates plain Success):
    1. roll == 20  → CritSuccess (always, no matter the target)
    2. roll == 1   → CritFail   (always, no matter the modifier)
    3. total >= target + _DECISIVE_MARGIN → CritSuccess
    4. total > target  → Success
    5. total == target → Tie
    6. total < target  → Fail
    """
    if roll == 20:
        return RollOutcome.CritSuccess
    if roll == 1:
        return RollOutcome.CritFail
    if total >= target + _DECISIVE_MARGIN:
        return RollOutcome.CritSuccess
    if total > target:
        return RollOutcome.Success
    if total == target:
        return RollOutcome.Tie
    return RollOutcome.Fail


@dataclass(frozen=True)
class SaveResult:
    """Fully resolved saving-throw result."""

    defender_actor: str
    category: SaveCategory
    target: int
    roll: int
    mod: int
    total: int
    shift: int
    tier: RollOutcome
    threat_label: str


def resolve_save(
    *,
    defender: EncounterActor,
    defender_class: str,
    pack_classes: Mapping[str, ClassDef],
    category: SaveCategory,
    ability: str | None,
    threat_label: str,
    rng: object,
) -> SaveResult:
    """Roll a B/X saving throw for *defender* against *category*.

    Parameters
    ----------
    defender:
        The ``EncounterActor`` making the save.
    defender_class:
        The class name key as it appears in *pack_classes* (e.g. ``"Mage"``).
    pack_classes:
        Full class registry from the loaded genre pack.
    category:
        Which of the five B/X columns to use (B26).
    ability:
        Ability stat name whose modifier is added to the roll (e.g. ``"WIS"``).
        Pass ``None`` to ignore the ability modifier (e.g. dragon breath).
    threat_label:
        Short human-readable label for the threat (e.g. ``"SLEEP"``).
    rng:
        Object with a ``randint(lo, hi)`` method.  Injected for deterministic
        testing; production callers pass ``random`` or a ``Random`` instance.

    Raises
    ------
    KeyError
        If *defender_class* is not in *pack_classes* (fail-loud, no fallback).
    ValueError
        If the matched ``ClassDef`` has no ``saving_throws`` table.
    ValueError
        If the actor's ``per_actor_state`` lacks the requested ability score.
    """
    if defender_class not in pack_classes:
        raise KeyError(
            f"Class '{defender_class}' not found in pack_classes "
            f"(available: {sorted(pack_classes)})"
        )
    class_def = pack_classes[defender_class]
    if class_def.saving_throws is None:
        raise ValueError(
            f"ClassDef '{defender_class}' has no saving_throws table — "
            "add a SavingThrowsTable to the class definition before "
            "resolving saves"
        )
    saving_throws = class_def.saving_throws
    target = saving_throws.target_for(category)
    roll: int = rng.randint(1, 20)
    mod = 0 if ability is None else _ability_modifier(_defender_score(defender, ability))
    total = roll + mod
    shift = total - target
    tier = _classify_save_tier(roll=roll, total=total, target=target)
    return SaveResult(
        defender_actor=defender.name,
        category=category,
        target=target,
        roll=roll,
        mod=mod,
        total=total,
        shift=shift,
        tier=tier,
        threat_label=threat_label,
    )


@dataclass(frozen=True)
class SpellEffectOutcome:
    """Result of applying a spell's save-interaction rule to a save tier."""

    applies_full_effect: bool
    applies_status: bool
    damage_multiplier: float


def apply_spell_effect(
    *,
    spell_effect: str,
    save_tier: RollOutcome | None,
) -> SpellEffectOutcome:
    """Map a spell's save-effect policy and a resolved save tier to an outcome.

    ``spell_effect`` vocabulary:

    - ``"none"``          — save has no effect; full damage/effect always applies.
    - ``"negates"``       — success negates; tie halves damage; fail full effect.
    - ``"halves"``        — crit-success blocks entirely; success quarters;
                            tie halves; fail full.
    - ``"partial:<tag>"`` — alias for ``"halves"`` (partial effects recurse).

    ``save_tier=None`` is treated identically to ``spell_effect="none"``:
    the spell always applies fully (used when no save was attempted).
    """
    if spell_effect == "none" or save_tier is None:
        return SpellEffectOutcome(
            applies_full_effect=True,
            applies_status=True,
            damage_multiplier=1.0,
        )

    if spell_effect == "negates":
        if save_tier in (RollOutcome.CritSuccess, RollOutcome.Success):
            return SpellEffectOutcome(
                applies_full_effect=False,
                applies_status=False,
                damage_multiplier=0.0,
            )
        if save_tier is RollOutcome.Tie:
            return SpellEffectOutcome(
                applies_full_effect=False,
                applies_status=False,
                damage_multiplier=0.5,
            )
        # Fail or CritFail
        return SpellEffectOutcome(
            applies_full_effect=True,
            applies_status=True,
            damage_multiplier=1.0,
        )

    if spell_effect == "halves":
        if save_tier is RollOutcome.CritSuccess:
            return SpellEffectOutcome(
                applies_full_effect=False,
                applies_status=False,
                damage_multiplier=0.0,
            )
        if save_tier is RollOutcome.Success:
            return SpellEffectOutcome(
                applies_full_effect=False,
                applies_status=False,
                damage_multiplier=0.25,
            )
        if save_tier is RollOutcome.Tie:
            return SpellEffectOutcome(
                applies_full_effect=False,
                applies_status=False,
                damage_multiplier=0.5,
            )
        # Fail or CritFail
        return SpellEffectOutcome(
            applies_full_effect=True,
            applies_status=True,
            damage_multiplier=1.0,
        )

    if spell_effect.startswith("partial:"):
        return apply_spell_effect(spell_effect="halves", save_tier=save_tier)

    raise ValueError(f"unknown spell_effect {spell_effect!r}")
