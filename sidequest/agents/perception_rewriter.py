"""PerceptionRewriter — deterministic fidelity+status-effect prose filter.

Scope clarification (Phase D / ADR-104): this module is the MP **fan-out**
span-strip pass that runs in ``sidequest/server/emitters.py`` per WS
recipient. It is **not** the narrator-path perception filter — narrator-side
perception filtering lives at the tool layer in
``sidequest/agents/narrator_perception_filter.py`` (Phase C). Both layers
coexist: the tool-layer filter shapes what the model SEES; this module
shapes what each recipient SEES on broadcast.

MP-ship version. LLM re-voicing on broadcast is deferred to post-MP (G10).
This module runs AFTER the projection filter produces a per-recipient
FilterDecision. Input: the canonical payload (already visibility-filtered).
Output: a payload with spans further stripped/annotated per the recipient's
status effects.

Composition order:
    canonical  ->  VisibilityTagFilter._apply_fidelity  ->  [FilterDecision]
                                                              |
                                                   PerceptionRewriter (this)
                                                              |
                                                         WS frame

Task-3's _apply_fidelity already handles fidelity bucket stripping. This
module layers status-effect overrides: a blinded recipient with
fidelity=full still has visual_only spans stripped because the status
effect trumps.
"""

from __future__ import annotations

from opentelemetry import trace

_tracer = trace.get_tracer("sidequest.perception_rewriter")


_STATUS_FIDELITY_OVERRIDE = {
    "blinded": "audio_only",
    "deafened": "visual_only",
    "invisible": None,  # self-invisibility affects OTHER viewers, not self
}


def _fidelity_for(
    base_fidelity: str,
    status_effects: list[str],
) -> str:
    """Status effects override fidelity if more restrictive."""
    for fx in status_effects:
        override = _STATUS_FIDELITY_OVERRIDE.get(fx)
        if override is not None:
            return override
    return base_fidelity


def _keep_span(span: dict, fidelity: str) -> bool:
    kind = span.get("kind", "full")
    if fidelity == "full":
        return True
    if fidelity == "audio_only":
        return kind != "visual_only"
    if fidelity == "visual_only":
        return kind != "audio_only"
    if fidelity == "audio_only_muffled":
        return kind != "visual_only"
    if fidelity == "periphery_only":
        return bool(span.get("periphery_tolerant"))
    if fidelity == "inferred_from_aftermath":
        return bool(span.get("aftermath"))
    return True


def rewrite_for_recipient(
    *,
    canonical_payload: dict,
    viewer_player_id: str,
    status_effects: dict[str, list[str]],
) -> dict:
    """Return a payload dict stripped by the viewer's effective fidelity."""
    viz = canonical_payload.get("_visibility", {}) or {}
    base = (viz.get("fidelity") or {}).get(viewer_player_id, "full")
    effective = _fidelity_for(base, status_effects.get(viewer_player_id, []))

    with _tracer.start_as_current_span("narrator.perception_rewrite") as span:
        span.set_attribute("viewer", viewer_player_id)
        span.set_attribute("base_fidelity", base)
        span.set_attribute("effective_fidelity", effective)
        span.set_attribute("status_effects", status_effects.get(viewer_player_id, []))

        spans = canonical_payload.get("spans")
        if not isinstance(spans, list) or effective == "full":
            return canonical_payload
        filtered = [s for s in spans if _keep_span(s, effective)]
        return {**canonical_payload, "spans": filtered}
