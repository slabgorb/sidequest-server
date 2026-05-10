"""Load per-room YAML files (ADR-096) → TacticalGridPayload.

Worlds use `<world_dir>/rooms/<room_id>.yaml` plus sibling
`.cavern.png` and `.mask.txt` artifacts emitted by the cavern_renderer
authoring tool.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sidequest.protocol.models import (
    CellularParams,
    DerivedRoomData,
    TacticalGridPayload,
)
from sidequest.server.asset_urls import resolve_asset_url
from sidequest.telemetry.spans.cavern_room import cavern_room_load_span


class RoomNotFoundError(Exception):
    """Raised when no <room_id>.yaml exists in the world's rooms/ dir."""


def load_room_payload(
    world_dir: Path,
    room_id: str,
    genre_slug: str = "caverns_and_claudes",
) -> TacticalGridPayload:
    """Load a room's metadata + mask, return a TacticalGridPayload.

    Tokens and initiative are not populated here — the dispatch layer
    fills them from game state.
    """
    yaml_path = world_dir / "rooms" / f"{room_id}.yaml"
    if not yaml_path.is_file():
        raise RoomNotFoundError(f"no room file at {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text())
    room_type = data.get("room_type")
    if room_type not in ("cavern", "settlement"):
        raise ValueError(
            f"{yaml_path}: invalid room_type {room_type!r}"
        )

    if room_type == "settlement":
        return TacticalGridPayload(
            room_id=room_id,
            room_name=data["name"],
            room_type="settlement",
            mask=None,
            cavern_image_url=None,
            cell_size=None,
            cellular=None,
            derived=None,
            tokens=[],
            initiative=None,
        )

    # Cavern path
    mask_path = world_dir / "rooms" / f"{room_id}.mask.txt"
    if not mask_path.is_file():
        raise FileNotFoundError(
            f"cavern room {yaml_path} missing sibling mask {mask_path}; "
            f"run `uv run cavern_renderer {yaml_path}` to regenerate"
        )
    mask = mask_path.read_text()

    cellular = data["cellular"]
    derived = data.get("derived")
    if derived is None:
        raise ValueError(
            f"{yaml_path}: cavern room missing 'derived:' block; "
            f"run cavern_renderer to populate"
        )

    relative = (
        f"genre_packs/{genre_slug}/worlds/{world_dir.name}/"
        f"rooms/{room_id}.cavern.png"
    )
    image_url = resolve_asset_url(relative)

    with cavern_room_load_span(
        room_id=room_id,
        seed=cellular["seed"],
        density=cellular.get("density", 0.55),
        floor_count=derived["floor_count"],
        mask=mask,
        cavern_image_url=image_url,
    ):
        pass

    return TacticalGridPayload(
        room_id=room_id,
        room_name=data["name"],
        room_type="cavern",
        mask=mask,
        cavern_image_url=image_url,
        cell_size=cellular.get("cell_size", 28),
        cellular=CellularParams(
            size=tuple(cellular["size"]),
            seed=cellular["seed"],
            density=cellular.get("density", 0.55),
            cutoff=cellular.get("cutoff", 5),
            passes=cellular.get("passes", 4),
        ),
        derived=DerivedRoomData(
            floor_count=derived["floor_count"],
            exits={
                k: (tuple(v) if v else None) for k, v in derived["exits"].items()
            },
            pois=[tuple(p) for p in derived["pois"]],
        ),
        tokens=[],
        initiative=None,
    )
