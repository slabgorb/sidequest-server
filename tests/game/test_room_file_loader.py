"""Tests for room_file_loader: filesystem → TacticalGridPayload."""

from pathlib import Path

import pytest

from sidequest.game.room_file_loader import (
    RoomNotFoundError,
    load_room_payload,
)
from sidequest.protocol.models import TacticalGridPayload


@pytest.fixture
def caverns_sunden_dir() -> Path:
    """Real path to the authored caverns_sunden world."""
    here = Path(__file__).resolve()
    repo = here.parents[3]  # oq-1
    return (
        repo
        / "sidequest-content"
        / "genre_packs"
        / "caverns_and_claudes"
        / "worlds"
        / "caverns_sunden"
    )


def test_load_cavern_room_returns_payload_with_image_and_mask(caverns_sunden_dir):
    payload = load_room_payload(caverns_sunden_dir, "mouth")
    assert isinstance(payload, TacticalGridPayload)
    assert payload.room_type == "cavern"
    assert payload.cavern_image_url is not None
    assert payload.cavern_image_url.endswith("/mouth.cavern.png")
    assert payload.mask is not None
    assert payload.cellular.seed == 1042
    assert payload.derived.floor_count > 0


def test_load_settlement_room_has_no_cavern_fields(caverns_sunden_dir):
    payload = load_room_payload(caverns_sunden_dir, "sunden_square")
    assert payload.room_type == "settlement"
    assert payload.cavern_image_url is None
    assert payload.mask is None


def test_load_unknown_room_raises(caverns_sunden_dir):
    with pytest.raises(RoomNotFoundError):
        load_room_payload(caverns_sunden_dir, "nonexistent_room")


def test_load_cavern_mask_matches_disk(caverns_sunden_dir):
    payload = load_room_payload(caverns_sunden_dir, "mouth")
    on_disk = (caverns_sunden_dir / "rooms" / "mouth.mask.txt").read_text()
    assert payload.mask == on_disk
