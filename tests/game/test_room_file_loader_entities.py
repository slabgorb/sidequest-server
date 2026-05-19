"""room_file_loader surfaces typed entities[] on TacticalGridPayload (Story 54-2).

Covers AC-4: load_room_payload parses the top-level entities: block from
the per-room YAML and exposes it on TacticalGridPayload.entities; rooms
without an entities: block produce an empty list (graceful absence).

These tests use synthetic tmp_path fixtures because no live genre pack
currently uses navigation_mode=room_graph with static room YAMLs — the
caverns_and_claudes world `beneath_sunden` is procedural (ADR-106) and
has no rooms/ directory. The loader is exercised here directly so the
new entities field is verified without depending on absent content.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sidequest.game.room_file_loader import load_room_payload
from sidequest.protocol.models import LocationEntity


def _make_world_dir(tmp_path: Path, *, room_yaml_body: str) -> Path:
    """Build a minimal world_dir with a settlement room YAML.

    Returns the world dir to pass to load_room_payload.
    """
    rooms = tmp_path / "rooms"
    rooms.mkdir(parents=True, exist_ok=True)
    (rooms / "synthetic.yaml").write_text(room_yaml_body)
    return tmp_path


def test_room_payload_exposes_entities_attribute(tmp_path):
    """AC-4 baseline: every loaded payload has a typed entities attribute."""
    world_dir = _make_world_dir(
        tmp_path,
        room_yaml_body=(
            "name: Synthetic Square\nroom_type: settlement\ndescription: A test room.\n"
        ),
    )
    payload = load_room_payload(world_dir, "synthetic")
    assert hasattr(payload, "entities"), "TacticalGridPayload must expose 'entities' per AC-4"
    assert isinstance(payload.entities, list)


def test_room_without_entities_defaults_empty(tmp_path):
    """AC-4: rooms with no 'entities:' block produce an empty list, not None."""
    world_dir = _make_world_dir(
        tmp_path,
        room_yaml_body=("name: Bare Room\nroom_type: settlement\ndescription: Nothing here.\n"),
    )
    payload = load_room_payload(world_dir, "synthetic")
    assert payload.entities == []


def test_room_with_entities_block_parses_typed(tmp_path):
    """AC-4: top-level entities: block is parsed into typed LocationEntity rows.

    Proves:
    1. The loader recognises the top-level entities: key.
    2. Dict-shaped YAML entries are coerced into LocationEntity instances.
    3. The manifest survives the loader → TacticalGridPayload transit.
    """
    world_dir = _make_world_dir(
        tmp_path,
        room_yaml_body=(
            "name: Sünden Square\n"
            "room_type: settlement\n"
            "description: A well at the centre, lit by a cobwebbed lantern.\n"
            "entities:\n"
            "  - id: square_well\n"
            "    label: the well at the centre\n"
            "    tier: real_object\n"
            "    binding:\n"
            "      kind: location_feature\n"
            "      ref: sunden_square_well\n"
            "    affordances:\n"
            "      - draw_water\n"
            "      - peer_into\n"
            "  - id: cobwebbed_lantern\n"
            "    label: a cobwebbed lantern\n"
            "    tier: flavor_only\n"
        ),
    )
    payload = load_room_payload(world_dir, "synthetic")
    assert len(payload.entities) == 2
    assert all(isinstance(e, LocationEntity) for e in payload.entities)
    by_id = {e.id: e for e in payload.entities}
    assert by_id["square_well"].tier == "real_object"
    assert by_id["square_well"].binding is not None
    assert by_id["square_well"].binding.kind == "location_feature"
    assert by_id["square_well"].affordances == ["draw_water", "peer_into"]
    assert by_id["cobwebbed_lantern"].tier == "flavor_only"
    assert by_id["cobwebbed_lantern"].binding is None


def test_loader_rejects_malformed_entity(tmp_path):
    """AC-4 negative: a bad entity in YAML surfaces a noisy ValidationError.

    Per CLAUDE.md "No Silent Fallbacks": a malformed entity must NOT be
    silently dropped — the loader rejects the room with a noisy error
    pointing at the offending row.
    """
    world_dir = _make_world_dir(
        tmp_path,
        room_yaml_body=(
            "name: Broken Room\n"
            "room_type: settlement\n"
            "description: A room.\n"
            "entities:\n"
            "  - id: x\n"
            "    label: x\n"
            "    tier: not_a_real_tier\n"
        ),
    )
    with pytest.raises(ValidationError) as excinfo:
        load_room_payload(world_dir, "synthetic")
    msg = str(excinfo.value).lower()
    assert "tier" in msg or "literal" in msg or "input" in msg, (
        f"loader must surface the validation error noisily; got: {excinfo.value!r}"
    )
