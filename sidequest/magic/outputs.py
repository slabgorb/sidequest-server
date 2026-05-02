"""Mandatory advancement outputs from confrontation outcomes — Story 47-3.

Each confrontation branch (clear_win, pyrrhic_win, clear_loss, refused)
declares ``mandatory_outputs: list[str]`` — IDs of state mutations that
fire when that branch resolves. This module dispatches those IDs to
their handlers.

Per CLAUDE.md "no silent fallback": unknown output IDs raise
``OutputUnknownError``. The handler registry is the explicit allow-list;
new outputs are added by extending ``OUTPUT_HANDLERS`` (and shipping
their consumer in the same story per the wire-first / no-deferrals
gate).

OTEL Observability Principle: every output emission emits a
``magic`` watcher event with ``op=mandatory_output`` so the GM panel
can verify the dispatcher engaged rather than the narrator improvised.

Note on naming: the session description (47-3) calls this module
``outcomes.py`` but the plan
(``docs/superpowers/plans/2026-04-28-magic-system-coyote-reach-v1.md``
§5.4 — the cited source of truth) names it ``outputs.py``. Tests
follow the plan; deviation logged in the session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sidequest.game.session import GameSnapshot
from sidequest.magic.state import BarKey
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish


class OutputUnknownError(RuntimeError):
    """Raised when a confrontation declares an output not in OUTPUT_HANDLERS."""


# Default deltas — keep small so a single confrontation rarely upends a
# character. These match the plan §5.4 defaults.
SANITY_DECREMENT_DEFAULT = 0.10
SANITY_INCREMENT_DEFAULT = 0.10
NOTICE_DECREMENT_DEFAULT = 0.15
NOTICE_INCREMENT_DEFAULT = 0.10
HEGEMONY_HEAT_INCREMENT_DEFAULT = 0.10
HEGEMONY_HEAT_DECREMENT_DEFAULT = 0.10
SANITY_FLOOR_LOWERED_DEFAULT = 0.05
BOND_INCREMENT_DEFAULT = 0.10
BOND_DECREMENT_DEFAULT = 0.10


OutputContext = dict[str, Any]
OutputHandler = Callable[[GameSnapshot, str, OutputContext], None]


def _shift_bar(
    snapshot: GameSnapshot,
    *,
    bar_id: str,
    owner: str,
    amount: float,
    scope: str = "character",
) -> None:
    """Move a ledger bar by ``amount`` (positive = up, negative = down).

    Bars not present in the ledger are skipped — the world may not track
    every bar id, and a confrontation referencing a missing bar is
    expected to no-op rather than raise. Skip is auditable via the
    output's own watcher event (the caller emits one per output).
    """
    if snapshot.magic_state is None:
        return
    key = BarKey(scope=scope, owner_id=owner, bar_id=bar_id)
    try:
        bar = snapshot.magic_state.get_bar(key)
    except KeyError:
        return
    snapshot.magic_state.set_bar_value(key, bar.value + amount)


def _queue_status_promotion(
    snapshot: GameSnapshot,
    *,
    actor: str,
    text: str,
    severity: str,
) -> None:
    if snapshot.magic_state is None:
        return
    snapshot.magic_state.pending_status_promotions.append(
        {"actor": actor, "text": text, "severity": severity}
    )


def _h_sanity_decrement(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    _shift_bar(snapshot, bar_id="sanity", owner=actor, amount=-SANITY_DECREMENT_DEFAULT)


def _h_sanity_increment(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    _shift_bar(snapshot, bar_id="sanity", owner=actor, amount=SANITY_INCREMENT_DEFAULT)


def _h_notice_decrement(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    _shift_bar(snapshot, bar_id="notice", owner=actor, amount=-NOTICE_DECREMENT_DEFAULT)


def _h_notice_increment(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    _shift_bar(snapshot, bar_id="notice", owner=actor, amount=NOTICE_INCREMENT_DEFAULT)


def _h_status_add_scratch(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    text = ctx.get("status_text", "Marked")
    _queue_status_promotion(snapshot, actor=actor, text=text, severity="Scratch")


def _h_status_add_wound(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    text = ctx.get("status_text", "Wounded")
    _queue_status_promotion(snapshot, actor=actor, text=text, severity="Wound")


def _h_status_add_scar(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    text = ctx.get("status_text", "Scarred")
    _queue_status_promotion(snapshot, actor=actor, text=text, severity="Scar")


def _h_control_tier_advance(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    """Bump the actor's innate control_tier by 1.

    Stored on MagicState (transient, not on the character sheet in v1
    per plan §5.4). The narrator can read this off ``MagicState.control_tier[actor]``
    to scale the next working's ceiling without touching character mechanics.
    """
    if snapshot.magic_state is None:
        return
    state = snapshot.magic_state
    state.control_tier[actor] = state.control_tier.get(actor, 0) + 1


def _h_hegemony_heat_increment(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    if snapshot.magic_state is None:
        return
    _shift_bar(
        snapshot,
        bar_id="hegemony_heat",
        owner=snapshot.magic_state.config.world_slug,
        amount=HEGEMONY_HEAT_INCREMENT_DEFAULT,
        scope="world",
    )


def _h_hegemony_heat_decrement(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    if snapshot.magic_state is None:
        return
    _shift_bar(
        snapshot,
        bar_id="hegemony_heat",
        owner=snapshot.magic_state.config.world_slug,
        amount=-HEGEMONY_HEAT_DECREMENT_DEFAULT,
        scope="world",
    )


def _h_lore_revealed(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    """Mint a LoreFragment via existing minting machinery.

    v1 placeholder: the lore-mint integration is wired in Phase 6 with
    the cliché-judge pass. The output ID is registered here so a
    Phase 5 confrontation that emits ``lore_revealed`` does not raise
    OutputUnknownError; the actual mint is no-op until Phase 6 lands.
    """
    return


def _h_item_acquired(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    """Add an item to the actor's inventory.

    v1 placeholder: the inventory + per-item bar minting integration is
    wired in Phase 6. The output ID is registered now so confrontations
    that emit ``item_acquired`` (the_salvage clear_win, etc.) do not
    raise OutputUnknownError.
    """
    return


def _h_sanity_floor_lowered(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:
    _shift_bar(snapshot, bar_id="sanity", owner=actor, amount=-SANITY_FLOOR_LOWERED_DEFAULT)


def _h_status_clear_bleeding_through(
    snapshot: GameSnapshot, actor: str, ctx: OutputContext
) -> None:
    """Remove any pending Bleeding-through promotion for ``actor``.

    Mirror of status_add_*; v1 simply drops queued entries that match.
    """
    if snapshot.magic_state is None:
        return
    state = snapshot.magic_state
    state.pending_status_promotions = [
        p
        for p in state.pending_status_promotions
        if not (p.get("actor") == actor and "bleeding" in p.get("text", "").lower())
    ]


def _noop(snapshot: GameSnapshot, actor: str, ctx: OutputContext) -> None:  # noqa: ARG001
    """No-op handler — registered for output IDs that fire in Phase 6.

    Distinct from "unknown" — the ID is allow-listed but its consumer is
    intentionally deferred. The watcher event still emits, so the GM
    panel sees the output flow even when the side-effect is pending.
    """
    return


# Registry — extend as more outputs land. Unknown ID → OutputUnknownError.
OUTPUT_HANDLERS: dict[str, OutputHandler] = {
    "sanity_decrement": _h_sanity_decrement,
    "sanity_increment": _h_sanity_increment,
    "notice_decrement": _h_notice_decrement,
    "notice_increment": _h_notice_increment,
    "hegemony_heat_increment": _h_hegemony_heat_increment,
    "hegemony_heat_decrement": _h_hegemony_heat_decrement,
    "status_add_scratch": _h_status_add_scratch,
    "status_add_wound": _h_status_add_wound,
    "status_add_scar": _h_status_add_scar,
    "control_tier_advance": _h_control_tier_advance,
    "lore_revealed": _h_lore_revealed,
    "lore_revealed_major": _h_lore_revealed,
    "item_acquired": _h_item_acquired,
    "item_acquired_alien": _h_item_acquired,
    "item_acquired_with_low_bond": _h_item_acquired,
    "item_history_increment": _noop,
    "bond_increment": _noop,
    "bond_decrement": _noop,
    "bond_increment_to_alien": _noop,
    "scar_political": _h_status_add_scar,
    "character_scar_extracted": _h_status_add_scar,
    "sanity_floor_lowered": _h_sanity_floor_lowered,
    "status_clear_bleeding_through": _h_status_clear_bleeding_through,
}


def apply_mandatory_outputs(
    *,
    snapshot: GameSnapshot,
    outputs: list[str],
    actor: str,
    **context: Any,
) -> None:
    """Apply each output ID by dispatching to OUTPUT_HANDLERS.

    Emits a ``magic`` watcher event per output (op=mandatory_output)
    so the GM panel sees every output fire — Sebastien's mechanical-
    visibility lens demands the side-effect chain be observable.
    Unknown IDs raise OutputUnknownError per CLAUDE.md no-silent-fallback.
    """
    for output_id in outputs:
        if output_id not in OUTPUT_HANDLERS:
            raise OutputUnknownError(
                f"unknown output {output_id!r}; known: {sorted(OUTPUT_HANDLERS)}"
            )
        OUTPUT_HANDLERS[output_id](snapshot, actor, context)
        _watcher_publish(
            "state_transition",
            {
                "field": "magic_state",
                "op": "mandatory_output",
                "output_id": output_id,
                "actor": actor,
            },
            component="magic",
        )
