"""Cleric reliquary invocation — divine_favor-gated free effect.

A reliquary is a Three-Rites relic the Cleric carries; at high
``divine_favor`` they may invoke it for a single free narrative effect
per session. The effect text lives on the reliquary entry in the
world's ``items.yaml`` (``reliquaries[].divine_favor_effect``) and the
narrator consumes it directly. The op surfaced here owns the mechanical
gate: bar check, once-per-session token, OTEL span. The narrator owns
the prose.

Gate order:

1. Actor has a ``divine_favor`` ledger bar (i.e. is a Cleric).
2. ``divine_favor >= threshold`` (default 0.7).
3. Reliquary exists in the world's items catalog AND carries a
   ``divine_favor_effect`` field (a non-favor-gated reliquary is content
   that doesn't belong on this path — surface as a load error, not a
   silent skip).
4. Actor has not already spent their free reliquary use this session.

A successful invoke marks the actor's free use as spent (mutates
``state.reliquary_free_use_spent``) and emits a
``magic.invoke_reliquary`` watcher event so the GM panel can prove the
mechanism engaged rather than the narrator winging it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sidequest.magic.state import BarKey, MagicState
from sidequest.telemetry.watcher_hub import publish_event as _watcher_publish

if TYPE_CHECKING:
    # Guarded to break a circular import path:
    # sidequest.magic.__init__ → context_builder → reliquary_ops →
    # genre.models.items → genre.__init__ → … → game.migrations →
    # telemetry.spans (still loading). Runtime call sites pass a fully
    # constructed WorldItemsCatalog from the loader, so duck-typing
    # ``items_catalog.reliquaries`` is safe at call time.
    from sidequest.genre.models.items import WorldItem, WorldItemsCatalog

_log = logging.getLogger(__name__)


DEFAULT_DIVINE_FAVOR_THRESHOLD = 0.7


class ReliquaryInvokeError(ValueError):
    """Raised when a reliquary invocation fails any gate.

    Subclasses ``ValueError`` so existing magic op call sites that catch
    ``ValueError`` (e.g. ``turn_undead``) keep working without changes.
    The ``reason`` field carries a stable string code so the GM panel
    can render a typed block reason instead of parsing the message.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class InvokeReliquaryResult:
    """Outcome of a successful reliquary invocation.

    The narrator consumes ``effect_text`` verbatim to ground the next
    narration beat. ``divine_favor`` and ``reliquary_name`` are surfaced
    so the GM panel can correlate the watcher span with the narration.
    """

    actor: str
    reliquary_id: str
    reliquary_name: str
    divine_favor: float
    effect_text: str


def _find_reliquary(items_catalog: WorldItemsCatalog, reliquary_id: str) -> WorldItem | None:
    """Linear scan of the reliquaries section. World catalogs cap at
    ~dozens of reliquaries; an index isn't worth the lifecycle cost."""
    for entry in items_catalog.reliquaries:
        if entry.id == reliquary_id:
            return entry
    return None


def invoke_reliquary(
    state: MagicState,
    *,
    actor: str,
    reliquary_id: str,
    items_catalog: WorldItemsCatalog,
    threshold: float = DEFAULT_DIVINE_FAVOR_THRESHOLD,
) -> InvokeReliquaryResult:
    """Invoke a Cleric's reliquary for the session's one free effect.

    Raises ``ReliquaryInvokeError`` if any gate fails (no divine_favor
    bar, favor below threshold, reliquary unknown, reliquary not
    favor-gated, free use already spent). Mutates
    ``state.reliquary_free_use_spent`` on success.

    The narrator is responsible for actually rendering the effect — the
    op returns ``effect_text`` so a single call site can chain the
    mechanical gate to the prose without re-walking the catalog.
    """
    # Gate 1: actor must have a divine_favor bar (defines them as Cleric).
    favor_key = BarKey(scope="character", owner_id=actor, bar_id="divine_favor")
    try:
        bar = state.get_bar(favor_key)
    except KeyError as e:
        raise ReliquaryInvokeError(
            reason="no_divine_favor_bar",
            message=(
                f"actor {actor!r} has no divine_favor bar; only Clerics may invoke reliquaries"
            ),
        ) from e

    # Gate 2: divine_favor must be at or above the high threshold.
    if bar.value < threshold:
        raise ReliquaryInvokeError(
            reason="favor_below_threshold",
            message=(
                f"divine_favor {bar.value:.2f} is below the reliquary threshold "
                f"{threshold:.2f}; restore at the Confessional/Workhouse/Masquerade "
                "before invoking"
            ),
        )

    # Gate 3: reliquary must exist in the world's catalog AND carry a
    # divine_favor_effect. A reliquary entry without one is a content
    # authoring bug, not a runtime fallback — fail loud.
    reliquary = _find_reliquary(items_catalog, reliquary_id)
    if reliquary is None:
        known = [r.id for r in items_catalog.reliquaries]
        raise ReliquaryInvokeError(
            reason="unknown_reliquary",
            message=(
                f"no reliquary with id {reliquary_id!r} in world items catalog. "
                f"Known reliquaries: {known}"
            ),
        )
    effect_text = reliquary.model_dump().get("divine_favor_effect")
    if not effect_text:
        raise ReliquaryInvokeError(
            reason="reliquary_missing_effect",
            message=(
                f"reliquary {reliquary_id!r} does not declare "
                "divine_favor_effect; cannot be invoked on this path"
            ),
        )

    # Gate 4: once-per-session free use. The alms-bowl text is the
    # canonical spec for the semantics: "Spends the free-reliquary-
    # effect for the session." Session-scoped, NOT rest-scoped — does
    # not refresh when the actor rests.
    if actor in state.reliquary_free_use_spent:
        raise ReliquaryInvokeError(
            reason="free_use_already_spent",
            message=(f"actor {actor!r} has already spent the session's free reliquary invocation"),
        )

    # Spend the token and emit the watcher span. Order: spend first so
    # a downstream OTEL emitter that crashes still leaves the gate
    # closed (no double-invoke escape hatch).
    state.reliquary_free_use_spent.append(actor)

    _watcher_publish(
        "magic.invoke_reliquary",
        {
            "actor": actor,
            "reliquary_id": reliquary_id,
            "reliquary_name": reliquary.name,
            "divine_favor": bar.value,
            "threshold": threshold,
            "free_use_spent_actors": list(state.reliquary_free_use_spent),
        },
        component="magic",
    )

    return InvokeReliquaryResult(
        actor=actor,
        reliquary_id=reliquary_id,
        reliquary_name=reliquary.name,
        divine_favor=bar.value,
        effect_text=str(effect_text),
    )
