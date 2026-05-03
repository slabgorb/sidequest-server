"""Structural tests for the chassis interior SVG renderer.

We don't golden-match coordinates; we assert structural elements
(rooms, stations, actors) are present with the right `data-*` attrs.
Layout coords can change without breaking these tests.
"""

import xml.etree.ElementTree as ET

from sidequest.genre.models.chassis import (
    ChassisClass,
    InteriorRoomSpec,
    StationSpec,
)
from sidequest.interior.render import render_interior_svg


def _voidborn_freighter_class() -> ChassisClass:
    return ChassisClass(
        id="voidborn_freighter",
        display_name="Voidborn Freighter",
        **{"class": "freighter"},
        provenance="voidborn_built",
        scale_band="vehicular",
        crew_model="flexible_roles",
        interior_rooms=[
            InteriorRoomSpec(id="cockpit", display_name="Cockpit"),
            InteriorRoomSpec(id="engineering", display_name="Engineering"),
            InteriorRoomSpec(id="galley", display_name="Galley"),
            InteriorRoomSpec(id="deck_three_corridor", display_name="Deck Three Corridor"),
        ],
        stations=[
            StationSpec(id="command", display_name="Command", room="cockpit"),
            StationSpec(id="helm", display_name="Helm", room="cockpit"),
            StationSpec(id="weapons", display_name="Weapons", room="cockpit"),
            StationSpec(id="engineering_controls", display_name="Engineering", room="engineering"),
        ],
    )


def _kestrel_instance(name="Kestrel"):
    """Minimal stand-in matching the runtime ChassisInstance attributes
    the renderer reads. Real ChassisInstance has more fields, but the
    renderer only touches id, name, class_id."""
    class _C:
        pass
    c = _C()
    c.id = "kestrel"
    c.name = name
    c.class_id = "voidborn_freighter"
    return c


def _snapshot(*, characters=(), npcs=()):
    """A bare snapshot stand-in. Renderer reads characters/npcs and
    each one's current_room (if set) and core.name."""

    class _Actor:
        def __init__(self, name, room):
            self.core = type("C", (), {"name": name})()
            self.current_room = room

    class _Snap:
        pass
    s = _Snap()
    s.characters = [_Actor(n, r) for n, r in characters]
    s.npcs = [_Actor(n, r) for n, r in npcs]
    return s


def test_render_includes_all_four_rooms():
    svg = render_interior_svg(
        _voidborn_freighter_class(), _kestrel_instance(), _snapshot()
    )
    for room_id in ["cockpit", "engineering", "galley", "deck_three_corridor"]:
        assert f'data-room="{room_id}"' in svg, f"missing room {room_id}"


def test_render_includes_all_four_stations():
    svg = render_interior_svg(
        _voidborn_freighter_class(), _kestrel_instance(), _snapshot()
    )
    for sid in ["command", "helm", "weapons", "engineering_controls"]:
        assert f'data-station="{sid}"' in svg


def _actor_room_in_svg(svg: str, actor_name: str) -> str | None:
    """Return the data-room of the room-group containing the named actor."""
    root = ET.fromstring(svg)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    for room_g in root.findall(".//svg:g[@data-room]", ns):
        for actor_g in room_g.findall(".//svg:g[@data-actor]", ns):
            if actor_g.get("data-actor") == actor_name:
                return room_g.get("data-room")
    return None


def test_render_places_pc_in_their_room():
    svg = render_interior_svg(
        _voidborn_freighter_class(),
        _kestrel_instance(),
        _snapshot(characters=[("Rux", "galley")]),
    )
    assert _actor_room_in_svg(svg, "Rux") == "galley"


def test_render_falls_back_to_default_for_unset_pc():
    """PC with current_room=None lands in cockpit (chassis-class first room)."""
    svg = render_interior_svg(
        _voidborn_freighter_class(),
        _kestrel_instance(),
        _snapshot(characters=[("Rux", None)]),
    )
    assert _actor_room_in_svg(svg, "Rux") == "cockpit"


def test_render_falls_back_kestrel_npc_to_default_table():
    """kestrel_doc with current_room=None lands in galley (default table)."""
    svg = render_interior_svg(
        _voidborn_freighter_class(),
        _kestrel_instance(),
        _snapshot(npcs=[("kestrel_doc", None)]),
    )
    assert _actor_room_in_svg(svg, "kestrel_doc") == "galley"


def test_render_includes_chassis_name():
    svg = render_interior_svg(
        _voidborn_freighter_class(),
        _kestrel_instance(name="Kestrel"),
        _snapshot(),
    )
    assert "Kestrel" in svg


def test_render_distinguishes_pc_and_npc():
    svg = render_interior_svg(
        _voidborn_freighter_class(),
        _kestrel_instance(),
        _snapshot(
            characters=[("Rux", "cockpit")],
            npcs=[("kestrel_captain", "cockpit")],
        ),
    )
    assert 'data-actor-kind="pc"' in svg
    assert 'data-actor-kind="npc"' in svg
