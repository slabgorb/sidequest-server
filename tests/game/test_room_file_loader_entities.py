"""room_file_loader surfaces typed entities[] on TacticalGridPayload (Story 54-2).

Covers AC-4: load_room_payload parses the top-level entities: block from
the per-room YAML and exposes it on TacticalGridPayload.entities; rooms
without an entities: block produce an empty list (graceful absence).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.room_file_loader import load_room_payload
from sidequest.protocol.models import LocationEntity


@pytest.fixture
def caverns_sunden_dir() -> Path:
    """Path to the canonical beneath_sunden world dir.

    Resolved relative to the test file rather than CWD so the test works
    regardless of how pytest is invoked.
    """
    here = Path(__file__).resolve()
    repo = here.parents[3]
    world_dir = (
        repo
        / "sidequest-content"
        / "genre_packs"
        / "caverns_and_claudes"
        / "worlds"
        / "caverns_sunden"
    )
    if not world_dir.exists():
        pytest.skip(f"world dir not present in this checkout: {world_dir}")
    return world_dir


def test_room_payload_exposes_entities_attribute(caverns_sunden_dir):
    """AC-4 baseline: every loaded room payload has an entities attribute.

    Independent of whether the room has a manifest — the attribute must
    exist on every TacticalGridPayload.
    """
    # Pick any room with a settlement description.
    rooms_dir = caverns_sunden_dir / "rooms"
    room_files = sorted(rooms_dir.glob("*.yaml"))
    if not room_files:
        pytest.skip(f"no room yaml files in {rooms_dir}")
    payload = load_room_payload(caverns_sunden_dir, room_files[0].stem)
    # Attribute must exist; type must be list.
    assert hasattr(payload, "entities"), (
        "TacticalGridPayload must expose 'entities' per AC-4"
    )
    assert isinstance(payload.entities, list)


def test_room_without_entities_defaults_empty(caverns_sunden_dir):
    """AC-4: rooms with no 'entities:' block produce an empty list, not None."""
    rooms_dir = caverns_sunden_dir / "rooms"
    # Find a room that doesn't already have an entities block — most
    # caverns_sunden rooms predate Story 54-2 and should pass this test.
    for room_yaml in sorted(rooms_dir.glob("*.yaml")):
        content = room_yaml.read_text()
        if "\nentities:" in content or content.startswith("entities:"):
            continue
        payload = load_room_payload(caverns_sunden_dir, room_yaml.stem)
        assert payload.entities == [], (
            f"room {room_yaml.stem} has no entities: block; loader must "
            "default to []"
        )
        return
    pytest.skip("every caverns_sunden room already has an entities: block")


def test_room_with_entities_block_parses_typed(caverns_sunden_dir):
    """AC-4: the fixture room (sunden_square) carries a real typed manifest.

    The fixture is seeded as part of this story (Task 3 Step 5 in the
    plan). When this test passes, it proves that:
    1. The loader recognises the top-level entities: block.
    2. Dict-shaped YAML entries are coerced into LocationEntity instances.
    3. The manifest survives the loader → TacticalGridPayload transit.
    """
    sunden_square = caverns_sunden_dir / "rooms" / "sunden_square.yaml"
    if not sunden_square.exists():
        pytest.skip("sunden_square.yaml absent — Dev should seed per plan Task 3")
    payload = load_room_payload(caverns_sunden_dir, "sunden_square")
    assert len(payload.entities) >= 1, (
        "sunden_square.yaml must be seeded with at least one entity for "
        "the wiring test — see plan Task 3 Step 5"
    )
    assert all(isinstance(e, LocationEntity) for e in payload.entities)
    # The seeded fixture per plan contains a real_object well + a flavor_only
    # lantern. We assert structural shape, not exact strings, so subsequent
    # fixture polish doesn't break the test.
    tiers = {e.tier for e in payload.entities}
    assert tiers.issubset({"real_object", "yes_and", "flavor_only"})


def test_loader_rejects_malformed_entity(caverns_sunden_dir, tmp_path, monkeypatch):
    """AC-4 negative: a bad entity in YAML surfaces a noisy ValidationError.

    No silent fallback — per CLAUDE.md. If a room yaml has a malformed
    entity, the loader must NOT silently drop it.
    """
    # Build a synthetic room yaml with one invalid entity (unknown tier).
    bad_room_dir = tmp_path / "rooms"
    bad_room_dir.mkdir(parents=True)
    bad_room_yaml = bad_room_dir / "broken_room.yaml"
    bad_room_yaml.write_text(
        "id: broken_room\n"
        "room_type: settlement\n"
        "settlement_description: 'A room.'\n"
        "entities:\n"
        "  - id: x\n"
        "    label: x\n"
        "    tier: not_a_real_tier\n"
    )
    # The test world dir is tmp_path; the loader rejects bad entity tiers
    # with a ValidationError surfaced from pydantic (any exception class
    # carrying 'tier' in its message qualifies — the contract is noisy
    # failure, not silent acceptance).
    with pytest.raises(Exception) as excinfo:
        load_room_payload(tmp_path, "broken_room")
    msg = str(excinfo.value).lower()
    assert "tier" in msg or "validation" in msg or "literal" in msg, (
        f"loader must surface the validation error noisily; got: {excinfo.value!r}"
    )
