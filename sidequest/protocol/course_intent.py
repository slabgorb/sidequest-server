"""Sidecar JSON variants for plot_course / cancel_course.

Carried inside the narrator's ``game_patch`` block, parsed by
narration_apply, dispatched to handlers/course_intent.py.

Not a new WebSocket message kind — STATE_PATCH (existing) carries
the resulting snapshot mutation back to the client.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class PlotCourseSidecar(BaseModel):
    """Narrator: 'plot a course to <body>'."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["plot_course"] = "plot_course"
    course_id: str


class CancelCourseSidecar(BaseModel):
    """Narrator: 'cancel the current plot'."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["cancel_course"] = "cancel_course"


CourseSidecar = PlotCourseSidecar | CancelCourseSidecar


def parse_course_sidecar(payload: Any) -> CourseSidecar | None:
    """Tolerant parser: returns ``None`` if the payload is not a course
    intent (so other sidecar handlers can run on the same game_patch).

    Validates the shape strictly when intent is course-related — bad
    payloads raise ValidationError (caught upstream and logged as
    rejected sidecars). Missing fields = None, not exception, because
    we want to differentiate 'wasn't ours' from 'was ours but malformed'.
    """
    if not isinstance(payload, dict):
        return None
    intent = payload.get("intent")
    if intent == "plot_course":
        if "course_id" not in payload:
            return None
        return PlotCourseSidecar.model_validate(payload)
    if intent == "cancel_course":
        return CancelCourseSidecar.model_validate(payload)
    return None
