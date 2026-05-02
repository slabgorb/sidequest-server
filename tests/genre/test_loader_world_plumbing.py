"""Tests for World plumbing — chassis_instances + magic_register.

Plumbing P1 of canned-openings: ensure the loader populates these two
fields on the World pydantic model so the chargen-completion helper at
``sidequest/server/websocket_session_handler._populate_opening_directive_on_chargen_complete``
can read them as live attributes (its ``getattr(world, ..., default)``
fallbacks become productive once the fields exist).

Tests construct minimal valid world fixtures in ``tmp_path`` and invoke
``_load_single_world`` directly — that's the unit being exercised.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sidequest.genre.loader import _load_single_world
from sidequest.genre.magic_loader import LoaderError

# ---------------------------------------------------------------------------
# Fixture helpers — minimal valid set for a world dir.
# ---------------------------------------------------------------------------

_WORLD_YAML = textwrap.dedent(
    """\
    name: Testworld
    description: Synthetic test world for loader plumbing.
    starting_location: testtown
    """
)

_LORE_YAML = textwrap.dedent(
    """\
    world_name: Testworld
    history: A brief history of testing.
    geography: Flat. Featureless. Test-shaped.
    cosmology: Two suns, no moons, deterministic stars.
    """
)

_CARTOGRAPHY_YAML = textwrap.dedent(
    """\
    world_name: Testworld
    starting_region: testtown
    navigation_mode: region
    regions:
      testtown:
        name: Testtown
        summary: A region for tests.
        description: A flat plain with one inn and a notional river.
        terrain: settlement
        adjacent: []
    """
)

# Fallback solo + 'either' covers both Validator 7 needs (solo and MP)
# without any chargen background coverage requirement (Validator 8 is
# disabled when char_creation has no "background" scene).
_OPENINGS_YAML = textwrap.dedent(
    """\
    version: "1.0.0"
    world: testworld
    genre: testgenre
    openings:
      - id: solo_default
        triggers:
          mode: either
          min_players: 1
          max_players: 6
          backgrounds: []
        setting:
          location_label: testtown
          situation: Standing in the square at noon.
        establishing_narration: |
          The square is empty. The sun is high. You stand alone.
    """
)

_RIGS_YAML = textwrap.dedent(
    """\
    version: "0.1.0"
    world: testworld
    genre: testgenre
    chassis_instances:
      - id: kestrel
        name: "Kestrel"
        class: voidborn_freighter
        OCEAN: { O: 0.6, C: 0.7, E: 0.4, A: 0.5, N: 0.5 }
        interior_rooms: [cockpit, galley]
        bond_seeds:
          - character_role: player_character
            bond_strength_character_to_chassis: 0.45
            bond_strength_chassis_to_character: 0.45
            bond_tier_character: trusted
            bond_tier_chassis: trusted
    """
)

# Minimal genre + world magic.yaml. The composer only requires:
#  - genre: { genre, permitted_plugins, narrator_register }
#  - world: { world, active_plugins (subset of permitted) }
_GENRE_MAGIC_YAML = textwrap.dedent(
    """\
    genre: testgenre
    allowed_sources: [innate]
    permitted_plugins: [innate_v1]
    intensity:
      default: 0.3
    world_knowledge_default:
      primary: classified
    cost_types: [sanity]
    narrator_register: |
      Test register: magic is uncanny and rare in this synthetic world.
    """
)

_WORLD_MAGIC_YAML = textwrap.dedent(
    """\
    world: testworld
    genre: testgenre
    intensity: 0.25
    active_plugins: [innate_v1]
    cost_types_active: [sanity]
    """
)


def _write_minimal_world(world_dir: Path) -> None:
    """Drop the required-files set into ``world_dir`` (must already exist)."""
    (world_dir / "world.yaml").write_text(_WORLD_YAML, encoding="utf-8")
    (world_dir / "lore.yaml").write_text(_LORE_YAML, encoding="utf-8")
    (world_dir / "cartography.yaml").write_text(_CARTOGRAPHY_YAML, encoding="utf-8")
    (world_dir / "openings.yaml").write_text(_OPENINGS_YAML, encoding="utf-8")


def _make_world_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Construct ``<tmp>/genre/worlds/testworld/`` + minimal files.

    Returns ``(genre_root, world_path)``.
    """
    genre_root = tmp_path / "genre"
    world_path = genre_root / "worlds" / "testworld"
    world_path.mkdir(parents=True)
    _write_minimal_world(world_path)
    return genre_root, world_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_world_without_rigs_or_magic_has_empty_defaults(tmp_path: Path) -> None:
    """Bare world: chassis_instances == [] and magic_register == ''."""
    genre_root, world_path = _make_world_tree(tmp_path)

    world = _load_single_world(world_path, [], genre_root)

    assert world.chassis_instances == []
    assert world.magic_register == ""


def test_world_with_rigs_yaml_populates_chassis_instances(tmp_path: Path) -> None:
    """rigs.yaml present → World.chassis_instances reflects the parsed list."""
    genre_root, world_path = _make_world_tree(tmp_path)
    (world_path / "rigs.yaml").write_text(_RIGS_YAML, encoding="utf-8")

    world = _load_single_world(world_path, [], genre_root)

    assert len(world.chassis_instances) == 1
    inst = world.chassis_instances[0]
    assert inst.id == "kestrel"
    assert inst.name == "Kestrel"
    assert inst.chassis_class_id == "voidborn_freighter"
    assert inst.bond_seeds[0].bond_tier_chassis == "trusted"


def test_world_with_magic_yaml_populates_magic_register(tmp_path: Path) -> None:
    """Both genre + world magic.yaml present → narrator_register flows to World."""
    genre_root, world_path = _make_world_tree(tmp_path)
    (genre_root / "magic.yaml").write_text(_GENRE_MAGIC_YAML, encoding="utf-8")
    (world_path / "magic.yaml").write_text(_WORLD_MAGIC_YAML, encoding="utf-8")

    world = _load_single_world(world_path, [], genre_root)

    assert world.magic_register != ""
    assert "Test register" in world.magic_register
    assert "synthetic" in world.magic_register


def test_world_magic_only_at_world_tier_silent_skips(tmp_path: Path) -> None:
    """World ships magic.yaml but genre doesn't → silent skip (load_world_magic
    requires both files; absence of either is a deliberate authoring choice
    and must not raise)."""
    genre_root, world_path = _make_world_tree(tmp_path)
    (world_path / "magic.yaml").write_text(_WORLD_MAGIC_YAML, encoding="utf-8")
    # Note: NO genre_root/magic.yaml.

    world = _load_single_world(world_path, [], genre_root)

    assert world.magic_register == ""


def test_malformed_magic_yaml_propagates_loader_error(tmp_path: Path) -> None:
    """Authoring bug in magic.yaml → LoaderError propagates (no swallow)."""
    genre_root, world_path = _make_world_tree(tmp_path)
    (genre_root / "magic.yaml").write_text(_GENRE_MAGIC_YAML, encoding="utf-8")
    # Malformed: world activates a plugin not in genre's permitted list.
    bad_world_magic = textwrap.dedent(
        """\
        world: testworld
        genre: testgenre
        active_plugins: [not_a_real_plugin]
        """
    )
    (world_path / "magic.yaml").write_text(bad_world_magic, encoding="utf-8")

    with pytest.raises(LoaderError):
        _load_single_world(world_path, [], genre_root)
