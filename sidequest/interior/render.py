"""Server-side SVG renderer for the chassis interior map.

Layout is hardcoded for the voidborn_freighter (the Kestrel) — a
2x2 grid of rooms with the cockpit top-left. Generalizing across
chassis classes is out of scope until a second class ships.

Visual register matches the orbital chart (black ground, brass-amber
phosphor, white reserved for the party marker — see
sidequest.orbital.palette) so the Ship tab sits visually next to the
orbital tab.
"""

from __future__ import annotations

import svgwrite

# Importing orbital.render installs the data-* attribute passthrough on
# svgwrite's validator. Without this, svgwrite rejects data-room /
# data-station / data-actor at write time.
from sidequest.orbital import palette
from sidequest.orbital import render as _orbital_render  # noqa: F401  (validator side-effect)

# 2x2 hardcoded layout for voidborn_freighter, in viewBox coordinates.
# Coords are (x, y, width, height).
ROOM_LAYOUT: dict[str, tuple[float, float, float, float]] = {
    "cockpit": (40, 60, 280, 180),
    "engineering": (340, 60, 220, 180),
    "galley": (40, 260, 280, 180),
    "deck_three_corridor": (340, 260, 220, 180),
}

VIEWBOX = (0, 0, 600, 480)

# Default crew-NPC assignments for the Kestrel — ships when an NPC
# doesn't yet have a current_room set. Hardcoded by id; the renderer
# falls back to the chassis's first room for any NPC not in this map.
KESTREL_NPC_DEFAULT_ROOM: dict[str, str] = {
    "kestrel_captain": "cockpit",
    "kestrel_engineer": "engineering",
    "kestrel_doc": "galley",
    "kestrel_cook": "galley",
}


def _actor_room(
    actor,
    chassis_class,
    fallback_table: dict[str, str] | None = None,
) -> str | None:
    """Return the room the actor should be drawn in, or None if no room."""
    explicit = getattr(actor, "current_room", None)
    if explicit:
        return explicit
    if fallback_table is not None:
        actor_id = getattr(getattr(actor, "core", None), "name", None)
        if actor_id and actor_id in fallback_table:
            return fallback_table[actor_id]
    if chassis_class.interior_rooms:
        return chassis_class.interior_rooms[0].id
    return None


def render_interior_svg(
    chassis_class,
    chassis_instance,
    snapshot,
) -> str:
    """Return a complete SVG document for the chassis interior map."""
    dwg = svgwrite.Drawing(
        size=("600px", "480px"),
        viewBox=" ".join(str(v) for v in VIEWBOX),
    )
    dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=palette.BG))

    # Title bar — chassis instance name in display font.
    dwg.add(
        dwg.text(
            chassis_instance.name,
            insert=(20, 36),
            fill=palette.BRASS,
            font_family=palette.FONT_DISPLAY,
            font_size="22px",
        )
    )

    stations_by_room: dict[str, list] = {}
    for s in chassis_class.stations:
        stations_by_room.setdefault(s.room, []).append(s)

    actors_by_room: dict[str, list[tuple[str, str]]] = {}
    for pc in getattr(snapshot, "characters", []) or []:
        room = _actor_room(pc, chassis_class)
        if room:
            actor_name = getattr(getattr(pc, "core", None), "name", "?")
            actors_by_room.setdefault(room, []).append((actor_name, "pc"))
    for npc in getattr(snapshot, "npcs", []) or []:
        room = _actor_room(npc, chassis_class, KESTREL_NPC_DEFAULT_ROOM)
        if room:
            actor_name = getattr(getattr(npc, "core", None), "name", "?")
            actors_by_room.setdefault(room, []).append((actor_name, "npc"))

    for room in chassis_class.interior_rooms:
        coords = ROOM_LAYOUT.get(room.id)
        if coords is None:
            # Hardcoded layout doesn't know this room — render a thin
            # reserve slot so the operator notices something authored
            # but unmapped.
            coords = (20, 460, 100, 16)
        x, y, w, h = coords
        room_g = dwg.g(**{"data-room": room.id})

        room_g.add(
            dwg.rect(
                insert=(x, y),
                size=(w, h),
                fill="none",
                stroke=palette.BRASS,
                stroke_width=1.2,
            )
        )
        room_g.add(
            dwg.text(
                room.display_name,
                insert=(x + 8, y + 18),
                fill=palette.BRASS,
                font_family=palette.FONT_DISPLAY,
                font_size="14px",
            )
        )

        # Stations as small open circles along the top edge.
        for i, station in enumerate(stations_by_room.get(room.id, [])):
            sx = x + 24 + i * 64
            sy = y + 44
            station_g = dwg.g(**{"data-station": station.id})
            station_g.add(
                dwg.circle(
                    center=(sx, sy),
                    r=7,
                    fill="none",
                    stroke=palette.BRASS,
                    stroke_width=1.2,
                )
            )
            station_g.add(
                dwg.text(
                    station.display_name,
                    insert=(sx - 22, sy + 22),
                    fill=palette.DIM,
                    font_family=palette.FONT_NUMERIC,
                    font_size="9px",
                )
            )
            room_g.add(station_g)

        # Actor markers along the bottom of the room.
        for i, (actor_name, kind) in enumerate(actors_by_room.get(room.id, [])):
            ax = x + 28 + i * 64
            ay = y + h - 24
            color = palette.PARTY if kind == "pc" else palette.BRASS
            actor_g = dwg.g(**{"data-actor": actor_name, "data-actor-kind": kind})
            actor_g.add(
                dwg.circle(
                    center=(ax, ay),
                    r=8,
                    fill=color,
                    stroke=palette.BRASS,
                    stroke_width=1.0,
                )
            )
            actor_g.add(
                dwg.text(
                    actor_name,
                    insert=(ax - 28, ay + 18),
                    fill=palette.BRASS,
                    font_family=palette.FONT_NUMERIC,
                    font_size="10px",
                )
            )
            room_g.add(actor_g)

        dwg.add(room_g)

    return dwg.tostring()
