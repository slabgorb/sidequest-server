"""Render dispatch subsystem spans (Story 45-30).

Two spans cover the render trigger policy decision so the GM panel can
audit every render-or-not call. Per CLAUDE.md OTEL Observability
Principle, every backend fix that touches a subsystem MUST add OTEL
watcher events so the GM panel can verify the fix is working — these
spans are the lie-detector for the render trigger policy.

- ``render.trigger`` fires on EVERY call to
  ``WebSocketSessionHandler._maybe_dispatch_render`` — including the
  banter case where the policy returned ``none_policy``. Silence is
  the bug; the GM panel surfaces the negative confirmation that the
  policy ran. Sebastien (mechanical-first, watches the GM panel) needs
  to see *why* a turn rendered or didn't.

- ``render.policy_skip`` fires only on the ``NONE_POLICY`` branch —
  a focused filter for the silent-by-design banter case so the panel
  can surface a banter-density timeline distinct from the trigger
  stream.
"""

from __future__ import annotations

from ._core import SPAN_ROUTES, SpanRoute

SPAN_RENDER_TRIGGER = "render.trigger"
SPAN_ROUTES[SPAN_RENDER_TRIGGER] = SpanRoute(
    event_type="state_transition",
    component="render",
    extract=lambda span: {
        "field": "render",
        "op": "trigger",
        "reason": (span.attributes or {}).get("reason", ""),
        "eligible": (span.attributes or {}).get("eligible", False),
        "queued": (span.attributes or {}).get("queued", False),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "player_id": (span.attributes or {}).get("player_id", ""),
        "had_visual_scene": (span.attributes or {}).get("had_visual_scene", False),
        "subject_present": (span.attributes or {}).get("subject_present", False),
    },
)


SPAN_RENDER_POLICY_SKIP = "render.policy_skip"
SPAN_ROUTES[SPAN_RENDER_POLICY_SKIP] = SpanRoute(
    event_type="state_transition",
    component="render",
    extract=lambda span: {
        "field": "render",
        "op": "policy_skip",
        "reason": (span.attributes or {}).get("reason", "none_policy"),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "player_id": (span.attributes or {}).get("player_id", ""),
        # narrator_emitted_subject distinguishes "narrator didn't even
        # try to emit a visual_scene" from "narrator tried but no
        # policy match" — both legitimate banter cases the GM panel
        # may want to differentiate.
        "narrator_emitted_subject": (span.attributes or {}).get("narrator_emitted_subject", False),
    },
)
