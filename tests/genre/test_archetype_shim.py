"""Archetype axis-lookup shim tests.

Port of sidequest-genre/tests/archetype_axes_test.rs (behavior tests) and
sidequest-genre/src/archetype/shim.rs (inline tests).

Covers:
- Axis validation (unknown jungian, unknown rpg_role)
- Forbidden pairing (genre-level)
- World-forbidden pairing
- World funnel resolution
- Genre fallback resolution
- Pairing weight lookup on ArchetypeConstraints
- Funnel resolve() and is_forbidden() methods
"""

from __future__ import annotations

import pytest
import yaml

from sidequest.genre.archetype.shim import (
    ArchetypeResolution,
    ResolutionSource,
    resolve_archetype,
)
from sidequest.genre.error import GenreValidationError
from sidequest.genre.models.archetype_axes import BaseArchetypes
from sidequest.genre.models.archetype_constraints import ArchetypeConstraints, PairingWeight
from sidequest.genre.models.archetype_funnels import ArchetypeFunnels
from sidequest.protocol.provenance import Tier


# ---------------------------------------------------------------------------
# Fixtures (mirrors Rust inline test helpers)
# ---------------------------------------------------------------------------


def _base() -> BaseArchetypes:
    return BaseArchetypes.model_validate(yaml.safe_load("""
jungian:
  - id: sage
    drive: "Seeks truth"
    ocean_tendencies:
      openness: [7.0, 9.5]
      conscientiousness: [6.0, 8.0]
      extraversion: [2.0, 5.0]
      agreeableness: [4.0, 7.0]
      neuroticism: [3.0, 6.0]
    stat_affinity: [wisdom, intellect]
  - id: hero
    drive: "Proves worth"
    ocean_tendencies:
      openness: [5.0, 7.0]
      conscientiousness: [6.0, 8.5]
      extraversion: [6.0, 8.5]
      agreeableness: [5.0, 7.5]
      neuroticism: [2.0, 4.5]
    stat_affinity: [strength, endurance]
rpg_roles:
  - id: healer
    combat_function: "Restores allies"
    stat_affinity: [wisdom]
  - id: tank
    combat_function: "Absorbs damage"
    stat_affinity: [strength]
npc_roles:
  - id: mentor
    narrative_function: "Guides protagonist"
"""))


def _constraints() -> ArchetypeConstraints:
    return ArchetypeConstraints.model_validate(yaml.safe_load("""
valid_pairings:
  common:
    - [sage, healer]
    - [hero, tank]
  uncommon: []
  rare: []
  forbidden: []
genre_flavor:
  jungian: {}
  rpg_roles:
    healer:
      fallback_name: "Hedge Healer"
    tank:
      fallback_name: "Shield-Bearer"
npc_roles_available: [mentor]
"""))


def _funnels() -> ArchetypeFunnels:
    return ArchetypeFunnels.model_validate(yaml.safe_load("""
funnels:
  - name: Thornwall Mender
    absorbs:
      - [sage, healer]
    faction: Thornwall Convocation
    lore: "Itinerant healers"
    cultural_status: respected
additional_constraints:
  forbidden: []
"""))


# ---------------------------------------------------------------------------
# ArchetypeConstraints methods
# ---------------------------------------------------------------------------


def test_pairing_weight_common() -> None:
    c = _constraints()
    assert c.pairing_weight("sage", "healer") == PairingWeight.common


def test_pairing_weight_not_listed_returns_none() -> None:
    c = _constraints()
    assert c.pairing_weight("hero", "healer") is None


def test_pairing_weight_forbidden() -> None:
    c = ArchetypeConstraints.model_validate(yaml.safe_load("""
valid_pairings:
  common: []
  uncommon: []
  rare: []
  forbidden:
    - [sage, tank]
genre_flavor:
  jungian: {}
  rpg_roles: {}
npc_roles_available: []
"""))
    assert c.pairing_weight("sage", "tank") == PairingWeight.forbidden


def test_fallback_name_found() -> None:
    c = _constraints()
    assert c.fallback_name("healer") == "Hedge Healer"
    assert c.fallback_name("tank") == "Shield-Bearer"


def test_fallback_name_missing_returns_none() -> None:
    c = _constraints()
    assert c.fallback_name("dps") is None


# ---------------------------------------------------------------------------
# ArchetypeFunnels methods
# ---------------------------------------------------------------------------


def test_funnel_resolve_hit() -> None:
    f = _funnels()
    result = f.resolve("sage", "healer")
    assert result is not None
    assert result.name == "Thornwall Mender"


def test_funnel_resolve_miss() -> None:
    f = _funnels()
    assert f.resolve("hero", "tank") is None


def test_funnel_is_forbidden_false() -> None:
    f = _funnels()
    assert not f.is_forbidden("sage", "healer")


def test_funnel_is_forbidden_true() -> None:
    f = ArchetypeFunnels.model_validate(yaml.safe_load("""
funnels: []
additional_constraints:
  forbidden:
    - [sage, tank]
"""))
    assert f.is_forbidden("sage", "tank")
    assert not f.is_forbidden("hero", "healer")


# ---------------------------------------------------------------------------
# resolve_archetype — core shim
# ---------------------------------------------------------------------------


def test_resolves_via_world_funnel() -> None:
    result = resolve_archetype(
        "sage", "healer",
        _base(), _constraints(), _funnels(),
        "caverns_and_claudes", "grimvault",
    )
    assert result.resolved.name == "Thornwall Mender"
    assert result.resolved.faction == "Thornwall Convocation"
    assert "Itinerant" in result.resolved.lore
    assert result.source == ResolutionSource.world_funnel
    assert result.provenance.source_tier == Tier.world


def test_falls_back_to_genre() -> None:
    result = resolve_archetype(
        "hero", "tank",
        _base(), _constraints(), None,
        "caverns_and_claudes", None,
    )
    assert result.resolved.name == "Shield-Bearer"
    assert result.resolved.faction is None
    assert result.source == ResolutionSource.genre_fallback
    assert result.provenance.source_tier == Tier.genre


def test_falls_back_to_genre_when_funnel_no_match() -> None:
    result = resolve_archetype(
        "hero", "tank",
        _base(), _constraints(), _funnels(),  # funnels has no hero+tank entry
        "caverns_and_claudes", "grimvault",
    )
    assert result.resolved.name == "Shield-Bearer"
    assert result.source == ResolutionSource.genre_fallback


def test_fallback_uses_rpg_role_id_when_no_flavor() -> None:
    """When no genre flavor fallback_name, use the rpg_role id as the name."""
    constraints_no_flavor = ArchetypeConstraints.model_validate(yaml.safe_load("""
valid_pairings:
  common:
    - [hero, tank]
  uncommon: []
  rare: []
  forbidden: []
genre_flavor:
  jungian: {}
  rpg_roles: {}
npc_roles_available: []
"""))
    result = resolve_archetype(
        "hero", "tank",
        _base(), constraints_no_flavor, None,
        "test_genre", None,
    )
    # fallback: no genre flavor → use rpg_role id
    assert result.resolved.name == "tank"
    assert result.source == ResolutionSource.genre_fallback


def test_rejects_forbidden_pairing_genre_level() -> None:
    c = ArchetypeConstraints.model_validate(yaml.safe_load("""
valid_pairings:
  common: []
  uncommon: []
  rare: []
  forbidden:
    - [sage, tank]
genre_flavor:
  jungian: {}
  rpg_roles: {}
npc_roles_available: []
"""))
    with pytest.raises(GenreValidationError, match="Forbidden"):
        resolve_archetype("sage", "tank", _base(), c, None, "test_genre", None)


def test_rejects_world_forbidden_pairing() -> None:
    f = ArchetypeFunnels.model_validate(yaml.safe_load("""
funnels: []
additional_constraints:
  forbidden:
    - [sage, healer]
"""))
    with pytest.raises(GenreValidationError, match="World-forbidden"):
        resolve_archetype("sage", "healer", _base(), _constraints(), f, "test_genre", "testworld")


def test_rejects_unknown_jungian_axis() -> None:
    with pytest.raises(GenreValidationError, match="Unknown Jungian"):
        resolve_archetype("nonexistent", "healer", _base(), _constraints(), None, "test_genre", None)


def test_rejects_unknown_rpg_role_axis() -> None:
    with pytest.raises(GenreValidationError, match="Unknown RPG role"):
        resolve_archetype("sage", "nonexistent", _base(), _constraints(), None, "test_genre", None)


def test_resolution_carries_correct_weight_common() -> None:
    result = resolve_archetype(
        "sage", "healer",
        _base(), _constraints(), None,
        "test_genre", None,
    )
    assert result.weight == PairingWeight.common


def test_resolution_defaults_to_uncommon_when_not_listed() -> None:
    """Pairings not in any weight category default to uncommon."""
    result = resolve_archetype(
        "hero", "healer",  # not listed in _constraints() valid_pairings
        _base(), _constraints(), None,
        "test_genre", None,
    )
    assert result.weight == PairingWeight.uncommon


def test_resolution_carries_jungian_and_rpg_role() -> None:
    result = resolve_archetype(
        "sage", "healer",
        _base(), _constraints(), None,
        "test_genre", None,
    )
    assert result.resolved.jungian == "sage"
    assert result.resolved.rpg_role == "healer"


def test_resolution_type_is_archetype_resolution() -> None:
    result = resolve_archetype(
        "hero", "tank",
        _base(), _constraints(), None,
        "test_genre", None,
    )
    assert isinstance(result, ArchetypeResolution)


# ---------------------------------------------------------------------------
# Provenance tests
# ---------------------------------------------------------------------------


def test_provenance_genre_fallback_tier_and_file() -> None:
    """Genre fallback sets source_tier=Genre and file path ending in archetype_constraints.yaml."""
    result = resolve_archetype(
        "hero", "tank",
        _base(), _constraints(), None,
        "caverns_and_claudes", None,
    )
    assert result.provenance.source_tier == Tier.genre
    assert result.provenance.source_file.endswith("archetype_constraints.yaml")
    assert len(result.provenance.merge_trail) == 1
    assert result.provenance.merge_trail[0].tier == Tier.genre


def test_provenance_world_funnel_tier_and_file() -> None:
    """World funnel sets source_tier=World and file path containing archetype_funnels.yaml."""
    result = resolve_archetype(
        "sage", "healer",
        _base(), _constraints(), _funnels(),
        "caverns_and_claudes", "grimvault",
    )
    assert result.provenance.source_tier == Tier.world
    assert "archetype_funnels.yaml" in result.provenance.source_file
    assert len(result.provenance.merge_trail) == 1
    assert result.provenance.merge_trail[0].tier == Tier.world


def test_provenance_world_fallback_uses_genre_tier_when_funnel_misses() -> None:
    """When funnels don't match, provenance falls back to Genre tier."""
    result = resolve_archetype(
        "hero", "tank",
        _base(), _constraints(), _funnels(),  # no hero+tank funnel
        "caverns_and_claudes", "grimvault",
    )
    assert result.provenance.source_tier == Tier.genre


# ---------------------------------------------------------------------------
# Wiring test: shim symbols exported from sidequest.genre
# ---------------------------------------------------------------------------


def test_shim_wired_into_package() -> None:
    """resolve_archetype, ArchetypeResolution, ResolutionSource must be in sidequest.genre."""
    from sidequest.genre import ArchetypeResolution as AR  # noqa: F401
    from sidequest.genre import ResolutionSource as RS  # noqa: F401
    from sidequest.genre import resolve_archetype as ra  # noqa: F401
    assert callable(ra)
    assert issubclass(AR, AR)  # just verify import
    assert isinstance(RS.world_funnel, RS)
