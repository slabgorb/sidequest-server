"""Story 47-10 — innate_v1 cast resolution.

The C&C player-facing surface for spell casting. Resolves a Spell against
the catalog-driven save model and emits the innate_v1.cast OTEL span.

Pairs with:
  - sidequest.magic.learned_ops.cast — direct data-layer cast used by tests
    and any plugin that drives MagicWorkings without the player-facing beat
  - sidequest.game.beat_filter.beats_available_for — the prepared-list gate
    that decides whether the cast_spell beat is selectable in the first
    place
  - sidequest.server.narration_apply — the consumer that drains the
    spell_slots ledger bar via beat.resource_deltas (separate from this
    function; this function does NOT mutate the ledger)

Save branch (codified 2026-05-09):
  - save.stat is None  -> auto-apply effect_template, save_skipped=True,
    no opposed check, no save fields on the span.
  - save.stat is set   -> route to opposed-check resolver via the
    save_resolver callable; on success apply save.effect (negates =
    no effect, halves = partial, partial:<text> = authored partial);
    on fail apply full effect_template.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sidequest.magic.spell_catalog import Spell
from sidequest.telemetry.spans.magic import innate_v1_cast_span

SaveResult = Literal["success", "fail"]
SaveResolver = Callable[[str, str], SaveResult]


@dataclass
class CastResult:
    """Structured outcome of an innate_v1 spell cast.

    Mirrors the OTEL span attributes on innate_v1.cast — keep the field
    names aligned so the GM panel doesn't drift between the in-process
    result and the dashboard rendering.
    """

    actor_id: str
    spell_id: str
    validator_outcome: str  # "ok" | "rejected_<reason>"
    slot_consumed: bool
    save_skipped: bool
    save_stat: str | None = None
    save_result: SaveResult | None = None
    damage_applied: str | None = None
    effect_applied: str | None = None


def resolve_innate_v1_cast(
    *,
    spell: Spell,
    actor_id: str,
    target_id: str,
    slot_consumed: bool = True,
    save_resolver: SaveResolver | None = None,
) -> CastResult:
    """Resolve a single cast and emit the innate_v1.cast span.

    ``save_resolver`` is required when the spell has a non-None save.stat;
    callers wire it to the C&C opposed_check resolver in production.
    Tests pass a stub callable that returns "success" or "fail" for the
    branch they're exercising.
    """
    save = spell.save

    if save.stat is None:
        # Auto-apply branch (Magic Missile, Light, Cure Light Wounds, ...).
        result = CastResult(
            actor_id=actor_id,
            spell_id=spell.id,
            validator_outcome="ok",
            slot_consumed=slot_consumed,
            save_skipped=True,
            effect_applied=spell.effect_template,
        )
    else:
        # Opposed-check branch. The save_resolver is invoked with
        # (stat, target_id) — the production wiring uses the C&C
        # check resolver which has access to the full game state.
        if save_resolver is None:
            raise ValueError(
                f"spell {spell.id!r} has save.stat={save.stat!r} but no "
                f"save_resolver was provided to resolve_innate_v1_cast — "
                f"the caller must pass a resolver for non-null-stat spells."
            )
        outcome: SaveResult = save_resolver(save.stat, target_id)
        if outcome == "success":
            # Apply save.effect's reduction. negates -> no effect; halves
            # -> partial (the narrator interprets); partial:<text> ->
            # authored partial. v1: emit None for negates, the partial
            # text for partial:, and "halves" sentinel for halves.
            if save.effect == "negates":
                effect_applied: str | None = None
            elif save.effect.startswith("partial:"):
                effect_applied = save.effect.split(":", 1)[1]
            else:  # halves
                effect_applied = f"halves: {spell.effect_template}"
        else:
            # Defender failed — full effect lands.
            effect_applied = spell.effect_template

        result = CastResult(
            actor_id=actor_id,
            spell_id=spell.id,
            validator_outcome="ok",
            slot_consumed=slot_consumed,
            save_skipped=False,
            save_stat=save.stat,
            save_result=outcome,
            effect_applied=effect_applied,
        )

    # OTEL: emit on every cast (success path; failure paths emit
    # innate_v1.cast_rejected_* by separate spans not modelled here).
    with innate_v1_cast_span(
        actor_id=result.actor_id,
        spell_id=result.spell_id,
        validator_outcome=result.validator_outcome,
        slot_consumed=result.slot_consumed,
        save_skipped=result.save_skipped,
        save_stat=result.save_stat,
        save_result=result.save_result,
        damage_applied=result.damage_applied,
    ):
        pass

    return result
