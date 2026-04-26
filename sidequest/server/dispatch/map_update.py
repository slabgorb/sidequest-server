"""MAP_UPDATE construction helpers — slice 1 of N from Rust port (ADR-082).

Slice 1 ports the cartography-render → ``MAP_UPDATE`` emission only. The
location-change and reconnect-replay paths from the original Rust impl
(``crates/sidequest-server/src/dispatch/{response,mod,connect}.rs``) are
intentionally deferred — see pingpong 2026-04-26 ``[S3-PORT-REGRESSION]``
``MAP_UPDATE`` for the deferred-with-spec follow-ups.

The wire payload shape is the existing ``MapUpdatePayload`` declared in
``sidequest/protocol/messages.py``; this module is purely a transform from
genre-pack ``CartographyConfig`` plus ``GameSnapshot`` into that wire shape.
No new payload models are introduced.

This module is *not* registered in ``_KIND_TO_MESSAGE_CLS`` — slice 1 emits
directly to the per-player outbound queue (mirroring the IMAGE async-emit
pattern). MAP_UPDATE is rebuildable from world state on resume, so journaling
is deferred until there's a concrete reconnect-with-map test failure.
"""

from __future__ import annotations

from sidequest.game.session import GameSnapshot
from sidequest.genre.models.world import (
    CartographyConfig,
    NavigationMode,
    Region,
    Route,
)
from sidequest.protocol.messages import MapUpdatePayload
from sidequest.protocol.models import (
    CartographyMetadata,
    CartographyRegion,
    CartographyRoute,
    ExploredLocation,
)
from sidequest.protocol.types import NonBlankString


def cartography_metadata_from_config(
    cartography: CartographyConfig | None,
) -> CartographyMetadata | None:
    """Translate a genre-pack ``CartographyConfig`` into wire ``CartographyMetadata``.

    Returns ``None`` when the input is missing — the wire payload's
    ``cartography`` field is optional, and a None means "no metadata to
    deliver" rather than "empty metadata."

    Lossy by design: the Rust wire model only carries the fields the UI
    consumes (``navigation_mode`` string, region dict, route list). Authorial
    flavor fields like ``map_style``, ``world_graph``, etc. stay server-side.
    """
    if cartography is None:
        return None

    regions: dict[str, CartographyRegion] = {}
    for slug, region in cartography.regions.items():
        if not isinstance(region, Region):
            continue
        # Wire model requires non-blank name; skip silently when the pack
        # left it blank (loader should have caught it, but don't crash here).
        try:
            name_nbs = NonBlankString(region.name)
        except ValueError:
            continue
        regions[slug] = CartographyRegion(
            name=name_nbs,
            description=region.description or "",
            adjacent=list(region.adjacent or []),
        )

    routes: list[CartographyRoute] = []
    for route in cartography.routes:
        if not isinstance(route, Route):
            continue
        try:
            name_nbs = NonBlankString(route.name)
        except ValueError:
            continue
        routes.append(
            CartographyRoute(
                name=name_nbs,
                description=route.description or "",
                from_id=route.from_id,
                to_id=route.to_id,
            )
        )

    nav_mode = cartography.navigation_mode
    if isinstance(nav_mode, NavigationMode):
        nav_mode_str = nav_mode.value
    else:
        nav_mode_str = str(nav_mode or NavigationMode.region.value)

    return CartographyMetadata(
        navigation_mode=nav_mode_str,
        starting_region=cartography.starting_region or "",
        regions=regions,
        routes=routes,
    )


def explored_locations_from_snapshot(
    snapshot: GameSnapshot,
    cartography: CartographyConfig | None,
) -> list[ExploredLocation]:
    """Build the ``explored`` list from snapshot's discovered_regions.

    Slice 1 is region-mode only: ``build_room_graph_explored`` (Rust
    ``crates/sidequest-game/src/room_movement.rs``) is not yet ported on
    the Python side — see ``sidequest/game/room_movement.py`` docstring,
    which calls out the runtime-movement port as a later story. When that
    lands, this helper grows a room-graph branch.

    The current location is flagged via ``is_current_room`` so the UI's
    Automapper can highlight it without needing to compare strings.
    """
    current = (snapshot.location or "").strip()
    explored: list[ExploredLocation] = []

    discovered = list(snapshot.discovered_regions or [])
    # Always include the current location even if discovery tracking missed it
    # — better to surface "you are here" with no neighbors than to omit it.
    if current and current not in discovered:
        discovered.append(current)

    for name in discovered:
        try:
            name_nbs = NonBlankString(name)
        except ValueError:
            continue

        connections: list[str] = []
        if cartography is not None and name in cartography.regions:
            region = cartography.regions[name]
            if isinstance(region, Region):
                connections = list(region.adjacent or [])

        explored.append(
            ExploredLocation(
                id=name,
                name=name_nbs,
                x=0,
                y=0,
                type="",  # alias for location_type
                connections=connections,
                is_current_room=(name == current),
            )
        )

    return explored


def build_map_update_payload(
    *,
    snapshot: GameSnapshot,
    cartography: CartographyConfig | None,
) -> MapUpdatePayload | None:
    """Construct a wire ``MapUpdatePayload`` from snapshot + world cartography.

    Returns ``None`` when the snapshot has no current location — the wire
    model requires both ``current_location`` and ``region`` to be non-blank,
    and emitting an empty MAP_UPDATE would be worse than skipping (the UI
    Automapper would clear its current-room highlight).
    """
    location = (snapshot.location or "").strip()
    if not location:
        return None

    try:
        loc_nbs = NonBlankString(location)
    except ValueError:
        return None

    region_raw = (snapshot.current_region or location).strip() or location
    try:
        region_nbs = NonBlankString(region_raw)
    except ValueError:
        region_nbs = loc_nbs

    return MapUpdatePayload(
        current_location=loc_nbs,
        region=region_nbs,
        explored=explored_locations_from_snapshot(snapshot, cartography),
        fog_bounds=None,
        cartography=cartography_metadata_from_config(cartography),
    )


__all__ = [
    "build_map_update_payload",
    "cartography_metadata_from_config",
    "explored_locations_from_snapshot",
]
