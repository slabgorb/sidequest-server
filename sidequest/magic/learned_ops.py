"""learned_v1 plugin operations — prepare, cast, rest, turn_undead.

Free functions on MagicState. The orchestrator calls prepare() when the
player declares "I prepare spells" at a safe site; cast() runs as a
narration_apply mutation when the narrator emits a learned_v1 working;
rest() restores slot bars and clears prepared_spells. turn_undead() is
the Cleric class-special; not a spell, no slot consumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sidequest.magic.state import BarKey, MagicState

_log = logging.getLogger(__name__)


@dataclass
class PrepareResult:
    actor: str
    prepared: dict[int, list[str]]
    slots_used_per_level: dict[int, int]


def _slot_bar_key(actor: str, level: int) -> BarKey:
    return BarKey(scope="character", owner_id=actor, bar_id=f"slots_l{level}")


def prepare(state: MagicState, *, actor: str, prep: dict[int, list[str]]) -> PrepareResult:
    """Replace actor's prepared list. Validates: known + within slot budget.

    Raises ValueError on unknown spell or over-budget. On success, mutates
    state.prepared_spells[actor] to the new prep dict.
    """
    known = set(state.known_spells.get(actor, []))
    for level, spell_ids in prep.items():
        for sid in spell_ids:
            if sid not in known:
                raise ValueError(
                    f"spell {sid!r} not in known_spells for actor {actor!r} "
                    f"(known: {sorted(known)})"
                )
        # Slot budget check: bar.spec.range[1] is the per-rest max for this level.
        try:
            bar = state.get_bar(_slot_bar_key(actor, level))
        except KeyError as e:
            raise ValueError(
                f"actor {actor!r} has no slots_l{level} bar; class does not grant L{level} slots"
            ) from e
        # Budget is the bar's *current* value (= chargen value pre-rest, or
        # the post-rest max). Plan §5.1 inline test uses range=(0.0, 4.0)
        # with starts_at_chargen=2.0 and expects 3 spells to fail — the
        # current value is the per-rest budget, not the range maximum.
        max_slots = int(bar.value)
        if len(spell_ids) > max_slots:
            raise ValueError(
                f"prep level {level}: {len(spell_ids)} spells exceeds slot budget {max_slots}"
            )

    state.prepared_spells[actor] = prep
    # Reset slot bars to per-rest max — preparation refreshes the budget.
    # The per-rest max is ``starts_at_chargen`` (not ``range[1]``, which is
    # the absolute ceiling — see plan §5.2 cast test where post-prepare
    # ``bar.value == 2.0`` for ``starts_at_chargen=2.0, range=(0.0, 4.0)``).
    for level in prep:
        bar = state.get_bar(_slot_bar_key(actor, level))
        starts = bar.spec.starts_at_chargen
        max_value = float(starts) if not isinstance(starts, dict) else float(bar.value)
        state.set_bar_value(_slot_bar_key(actor, level), max_value)

    return PrepareResult(
        actor=actor,
        prepared=prep,
        slots_used_per_level={lvl: len(ids) for lvl, ids in prep.items()},
    )
