"""Data models for the render pipeline.

RenderTier: image tiers with distinct latency/quality tradeoffs.
StageCue: Backend-agnostic render request.
RenderResult: Render output with image path, dimensions, timing.

Mirrors sidequest_daemon.renderer.models — these are the wire contract
the server sends over the daemon socket, so the types must stay in sync.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class RenderTier(StrEnum):
    """Image generation tiers, each with distinct latency/quality tradeoffs."""

    TACTICAL_SKETCH = "tactical_sketch"
    SCENE_ILLUSTRATION = "scene_illustration"
    PORTRAIT = "portrait"
    PORTRAIT_SQUARE = "portrait_square"
    LANDSCAPE = "landscape"
    TEXT_OVERLAY = "text_overlay"
    CARTOGRAPHY = "cartography"
    FOG_OF_WAR = "fog_of_war"


class StageCue(BaseModel):
    """Backend-agnostic render request — what to draw."""

    tier: RenderTier
    subject: str
    mood: str = ""
    location: str = ""
    characters: list[str] = []
    tags: list[str] = []
    seed: int | None = None
    turn_id: int = 0
    metadata: dict[str, Any] = {}


class RenderResult(BaseModel):
    """Render output — where the image is and how long it took."""

    image_path: Path
    width: int
    height: int
    generation_time_ms: int
    tier: RenderTier
    cue: StageCue
    worker: str
