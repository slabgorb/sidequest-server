"""Unit tests for ``_resolve_location_display``.

Playtest 2026-04-24 — "Location name rendered as slug with underscores
(e.g. backstage_corridor)". The resolver turns a location id (or
narrator-invented slug) into a human-readable display name for the UI:
looks the id up in ``world.cartography.rooms`` first, falls back to
``humanize_snake_case`` on snake_case-looking strings, and returns the
input unchanged when it's already a display name.
"""

from __future__ import annotations

from pathlib import Path

from sidequest.genre.loader import load_genre_pack
from sidequest.server.session_handler import _resolve_location_display

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


def _load_cnc() -> object:
    return load_genre_pack(CONTENT_ROOT / "caverns_and_claudes")


def test_room_id_resolves_to_room_name() -> None:
    pack = _load_cnc()
    # rooms.yaml in primetime declares:
    #   - id: the_summoning_stage
    #     name: "The Summoning Stage"
    assert (
        _resolve_location_display(pack, "primetime", "the_summoning_stage")
        == "The Summoning Stage"
    )


def test_narrator_invented_slug_humanizes() -> None:
    pack = _load_cnc()
    # "backstage_corridor" is NOT a room id in primetime — narrator
    # confabulated it. We humanize so the UI header doesn't read
    # "backstage_corridor".
    assert (
        _resolve_location_display(pack, "primetime", "backstage_corridor")
        == "Backstage Corridor"
    )


def test_already_humanized_returns_unchanged() -> None:
    pack = _load_cnc()
    assert (
        _resolve_location_display(pack, "primetime", "The Green Room")
        == "The Green Room"
    )


def test_no_pack_falls_back_to_humanize() -> None:
    assert (
        _resolve_location_display(None, None, "mystery_compass_shop")
        == "Mystery Compass Shop"
    )


def test_empty_location_returns_empty() -> None:
    assert _resolve_location_display(None, None, "") == ""
    assert _resolve_location_display(None, None, None) == ""
