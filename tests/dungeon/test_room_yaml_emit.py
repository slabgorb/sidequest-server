"""write_room_yaml — durable per-region YAML emit (Story 55-1).

Covers AC-8: the helper writes a YAML at <world>/rooms/<id>.yaml whose
entities list re-validates as LocationEntity rows (the contract 54-2's
loader consumes), refuses to overwrite existing files when
overwrite=False (freeze invariant), and creates the rooms/ directory
when missing.

The dedicated round-trip-via-room_file_loader test covers AC-8's exact
loader-pairing claim; the helper must produce a YAML that
``load_room_payload`` accepts without ad-hoc shape massaging by the
caller.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sidequest.dungeon.room_yaml_emit import write_room_yaml
from sidequest.protocol.models import (
    LocationEntity,
    LocationEntityBinding,
)


def _entities() -> list[LocationEntity]:
    return [
        LocationEntity(
            id="echoing_pool",
            label="a black pool reflects the torchlight",
            tier="real_object",
            binding=LocationEntityBinding(kind="location_feature", ref="echoing_pool"),
            affordances=["drink_to_scry"],
            provenance="cookbook",
        ),
        LocationEntity(
            id="slick_walls",
            label="A slick green crust coats the walls.",
            tier="flavor_only",
            provenance="cookbook",
        ),
    ]


# ---------------------------------------------------------------------------
# File creation + directory handling
# ---------------------------------------------------------------------------


def test_write_creates_yaml_file(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="A narrow chamber slick with damp.",
        entities=_entities(),
    )
    assert (world_dir / "rooms" / "region_42.yaml").is_file()


def test_creates_rooms_directory_if_missing(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    assert not (world_dir / "rooms").exists()
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="anything",
        entities=[],
    )
    assert (world_dir / "rooms").is_dir()


def test_returns_path_to_written_file(tmp_path: Path) -> None:
    world_dir = tmp_path / "world"
    path = write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="x",
        entities=[],
    )
    assert path == world_dir / "rooms" / "region_42.yaml"
    assert path.is_file()


# ---------------------------------------------------------------------------
# AC-8: entities round-trip — the contract 54-2's loader path consumes
# ---------------------------------------------------------------------------


def test_persisted_yaml_carries_description_and_entities(tmp_path: Path) -> None:
    """Persisted shape: top-level 'description' + top-level 'entities' list.
    This is the contract 54-2's loader reads (data.get('entities') or [])."""
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="A narrow chamber slick with damp.",
        entities=_entities(),
    )
    data = yaml.safe_load((world_dir / "rooms" / "region_42.yaml").read_text())
    assert data["description"] == "A narrow chamber slick with damp."
    assert isinstance(data["entities"], list)
    assert len(data["entities"]) == 2


def test_persisted_entities_re_validate_as_location_entity(tmp_path: Path) -> None:
    """Producer/consumer share a contract: every persisted entity row must
    re-validate via LocationEntity.model_validate, with binding and
    affordances preserved on the real_object row and provenance preserved
    on every row."""
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="prose",
        entities=_entities(),
    )
    data = yaml.safe_load((world_dir / "rooms" / "region_42.yaml").read_text())
    restored = [LocationEntity.model_validate(row) for row in data["entities"]]

    pool = next(e for e in restored if e.id == "echoing_pool")
    assert pool.tier == "real_object"
    assert pool.binding is not None
    assert pool.binding.kind == "location_feature"
    assert pool.binding.ref == "echoing_pool"
    assert pool.affordances == ["drink_to_scry"]
    assert pool.provenance == "cookbook"

    walls = next(e for e in restored if e.id == "slick_walls")
    assert walls.tier == "flavor_only"
    assert walls.binding is None
    assert walls.provenance == "cookbook"


def test_round_trip_via_room_file_loader(tmp_path: Path) -> None:
    """AC-8 explicit: the helper's output is consumed by 54-2's
    ``load_room_payload`` (the same loader the dispatch layer uses to
    serve TacticalGridPayloads). This is the load-bearing
    interoperability contract — the materializer's output and the
    runtime loader's input MUST share a shape.

    The cavern-side path needs a sibling ``<id>.mask.txt`` and the
    minimal ``cellular``/``derived`` blocks that 52-2/52-3 already emit
    in production; we synthesize the smallest valid set here so the
    round-trip can exercise the entity-loading branch only.
    """
    from sidequest.game.room_file_loader import load_room_payload

    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="A narrow chamber slick with damp.",
        entities=_entities(),
    )
    payload = load_room_payload(world_dir, "region_42")
    assert len(payload.entities) == 2
    pool = next(e for e in payload.entities if e.id == "echoing_pool")
    assert pool.binding is not None
    assert pool.binding.ref == "echoing_pool"
    assert pool.provenance == "cookbook"


# ---------------------------------------------------------------------------
# AC-8: freeze-invariant — overwrite=False refuses existing files
# ---------------------------------------------------------------------------


def test_overwrite_false_refuses_existing_file(tmp_path: Path) -> None:
    """AC-8: an existing YAML on disk represents frozen content; the helper
    refuses to overwrite by default (matches ADR-106 §7 freeze invariant)."""
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="first",
        entities=[],
    )
    with pytest.raises(FileExistsError):
        write_room_yaml(
            world_dir=world_dir,
            room_id="region_42",
            description="second",
            entities=[],
            overwrite=False,
        )


def test_overwrite_false_is_the_default(tmp_path: Path) -> None:
    """Defensive: a caller that forgets the overwrite= kwarg must get the
    safe behaviour (No Silent Fallbacks). The default protects the
    freeze invariant against caller drift."""
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="first",
        entities=[],
    )
    with pytest.raises(FileExistsError):
        write_room_yaml(
            world_dir=world_dir,
            room_id="region_42",
            description="second",
            entities=[],
        )


def test_overwrite_true_replaces_existing_file(tmp_path: Path) -> None:
    """Tests / migrations may explicitly opt in to overwrite; production
    materializer NEVER passes overwrite=True."""
    world_dir = tmp_path / "world"
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="first",
        entities=[],
    )
    write_room_yaml(
        world_dir=world_dir,
        room_id="region_42",
        description="second",
        entities=[],
        overwrite=True,
    )
    yaml_path = world_dir / "rooms" / "region_42.yaml"
    assert "second" in yaml_path.read_text()
