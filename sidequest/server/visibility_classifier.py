"""Narration visibility classifier (Story 49-8).

Produces the ``_visibility`` sidecar dict that drives per-recipient
narration routing in :mod:`sidequest.server.emitters`. The classifier
runs at the post-narration emit site after the narrator subprocess has
returned — no extra LLM calls.

Found in the 2026-05-12 caverns_sunden playtest: the live
``WebSocketSessionHandler`` ships ``visibility_sidecar=None`` so every
player receives every per-PC narration card identically in third
person. This module fills the gap with the v2 sidecar shape:

    {
        "visible_to": "all" | [player_id, ...],
        "fidelity":   {entity_id: fidelity_level},
        "anchor_pc":  "Carl" | None,
        "pov_strategy": "pc_anchored" | "atmospheric" | "private",
    }

The v2 keys (``anchor_pc``, ``pov_strategy``) are purely additive to
the v1 shape; existing consumers
(:class:`sidequest.game.projection.rules.VisibilityTagRule`,
:func:`sidequest.server.session_helpers.aggregate_visibility`) ignore
keys they do not know about.

Anchor inference order:
  1. ``result.action_rewrite.named`` — the structured field the
     narrator emits per ADR-039. Validated against the snapshot's PC
     roster; NPC names are NOT accepted as anchors.
  2. First-sentence scan of ``result.narration`` for a PC name from the
     roster.
  3. No match → atmospheric (``anchor_pc=None``,
     ``pov_strategy="atmospheric"``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from sidequest.agents.orchestrator import NarrationTurnResult
    from sidequest.game.session import GameSnapshot

_tracer = trace.get_tracer("sidequest.visibility_classifier")


def _pc_names(snapshot: GameSnapshot) -> list[str]:
    """Return the list of PC names from the snapshot's character roster."""
    out: list[str] = []
    for c in snapshot.characters:
        name = (c.core.name or "").strip()
        if name:
            out.append(name)
    return out


def _first_sentence(text: str) -> str:
    """Return the first sentence of ``text`` (up to first .!? or end)."""
    m = re.match(r"[^.!?]+", text)
    return m.group(0) if m else text


def _find_pc_in_text(text: str, pc_names: list[str]) -> str | None:
    """Return the first PC name found in ``text`` (case-sensitive,
    word-boundary match), or None if no PC is mentioned.
    """
    for name in pc_names:
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def classify_narration_visibility(
    *,
    result: NarrationTurnResult,
    snapshot: GameSnapshot,
    connected_player_ids: list[str],
    player_id_to_character: dict[str, str],
) -> dict[str, Any]:
    """Build the v2 ``_visibility`` sidecar for a narration emit.

    Args:
        result: The orchestrator's ``NarrationTurnResult`` (carries the
            narration prose and the structured ``ActionRewrite`` field).
        snapshot: The current ``GameSnapshot`` — used only for its PC
            roster (``snapshot.characters``).
        connected_player_ids: Player IDs currently attached to sockets;
            reserved for future "visible_to" filtering (this story
            broadcasts to all).
        player_id_to_character: Mapping from player_id to PC name. Used
            by downstream consumers to map ``anchor_pc`` back to a
            player_id at swap-time; not used here directly.

    Returns:
        Dict with keys ``visible_to``, ``fidelity``, ``anchor_pc``,
        ``pov_strategy``. The dict is dropped onto
        ``NarrationPayload.visibility_sidecar`` (wire name
        ``_visibility``) at the emit site.

    Raises:
        ValueError: If ``result.narration`` is empty or whitespace-only.
            An empty narration is unrenderable upstream — fail loud
            rather than emit an ambiguous sidecar.
    """
    narration = (result.narration or "").strip()
    if not narration:
        raise ValueError("narration text cannot be empty")

    pc_roster = _pc_names(snapshot)

    # ------------------------------------------------------------------
    # Step 1: try action_rewrite.named. Must validate against PC roster.
    # ------------------------------------------------------------------
    anchor: str | None = None
    action_rewrite = getattr(result, "action_rewrite", None)
    if action_rewrite is not None:
        named = (getattr(action_rewrite, "named", "") or "").strip()
        if named:
            candidate = _find_pc_in_text(named, pc_roster)
            if candidate is not None:
                anchor = candidate

    # ------------------------------------------------------------------
    # Step 2: fallback to the first sentence of the prose.
    # ------------------------------------------------------------------
    if anchor is None:
        first = _first_sentence(narration)
        anchor = _find_pc_in_text(first, pc_roster)

    pov_strategy = "pc_anchored" if anchor is not None else "atmospheric"

    sidecar: dict[str, Any] = {
        # No per-recipient filtering for this story — ADR-028 (Perception
        # Rewriter) follow-up will tighten visible_to for private cards.
        "visible_to": "all",
        # Fidelity untouched — perception_rewriter already consumes this
        # shape and the new POV swap layer is orthogonal to fidelity.
        "fidelity": {},
        "anchor_pc": anchor,
        "pov_strategy": pov_strategy,
    }

    # OTEL lie-detector — the GM panel needs to see what anchor the
    # classifier resolved on every turn so a "narrator said Carl but
    # the swap fired on Donut" regression surfaces immediately.
    with _tracer.start_as_current_span("narration.visibility_classified") as span:
        # OTEL forbids None values; encode "no anchor" as empty string.
        span.set_attribute("anchor_pc", anchor or "")
        span.set_attribute("pov_strategy", pov_strategy)
        # visible_to either "all" (single sentinel string) or a
        # comma-joined player-id list. Joined form keeps the span
        # attribute scalar so it indexes cleanly.
        visible_to_val = sidecar["visible_to"]
        if isinstance(visible_to_val, list):
            span.set_attribute("visible_to", ",".join(visible_to_val))
        else:
            span.set_attribute("visible_to", str(visible_to_val))

    return sidecar
