"""magic_loader: yaml → WorldMagicConfig."""
from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.magic_loader import LoaderError, load_world_magic
from sidequest.magic.models import WorldMagicConfig

FIXTURES = Path(__file__).parent / "fixtures"
GENRE_YAML = FIXTURES / "space_opera_magic.yaml"
WORLD_YAML = FIXTURES / "coyote_reach_magic.yaml"


def test_load_world_magic_returns_config():
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    assert isinstance(config, WorldMagicConfig)
    assert config.world_slug == "coyote_reach"
    assert config.genre_slug == "space_opera"


def test_world_inherits_genre_allowed_sources():
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    assert "innate" in config.allowed_sources
    assert "item_based" in config.allowed_sources


def test_world_inherits_genre_hard_limits():
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    limit_ids = [hl.id for hl in config.hard_limits]
    assert "no_resurrection" in limit_ids
    assert "psionics_never_decisive" in limit_ids


def test_world_intensity_overrides_genre_default():
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    assert config.intensity == 0.25  # world override of genre default 0.3


def test_world_knowledge_subtag_loads():
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    assert config.world_knowledge.primary == "classified"
    assert config.world_knowledge.local_register == "folkloric"


def test_four_world_load_ledger_bars():
    """Coyote Reach v1 ships four world-load bars (3 character + 1 world).
    bond + item_history are per-item, instantiated per-item not at world-load."""
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    bar_ids = [b.id for b in config.ledger_bars]
    assert sorted(bar_ids) == ["hegemony_heat", "notice", "sanity", "vitality"]


def test_active_plugin_must_be_in_genre_permitted():
    """If a world declares an active plugin the genre doesn't permit, fail loud."""
    bad_world = WORLD_YAML.read_text(encoding="utf-8").replace(
        "active_plugins: [innate_v1, item_legacy_v1]",
        "active_plugins: [innate_v1, item_legacy_v1, divine_v1]",
    )
    bad_path = FIXTURES / "_bad_active.yaml"
    bad_path.write_text(bad_world, encoding="utf-8")
    try:
        with pytest.raises(LoaderError, match="divine_v1.*not.*permitted"):
            load_world_magic(genre_yaml=GENRE_YAML, world_yaml=bad_path)
    finally:
        bad_path.unlink(missing_ok=True)


def test_missing_genre_yaml_fails_loud():
    with pytest.raises(LoaderError, match="genre.*not found"):
        load_world_magic(genre_yaml=Path("/nonexistent.yaml"), world_yaml=WORLD_YAML)


def test_missing_world_yaml_fails_loud():
    with pytest.raises(LoaderError, match="world.*not found"):
        load_world_magic(genre_yaml=GENRE_YAML, world_yaml=Path("/nonexistent.yaml"))


def test_world_local_register_exceeding_primary_fails_loud():
    """Schema-level: local_register > primary in awareness ordering."""
    bad_world = WORLD_YAML.read_text(encoding="utf-8").replace(
        "local_register: folkloric",
        "local_register: acknowledged",
    )
    bad_path = FIXTURES / "_bad_local.yaml"
    bad_path.write_text(bad_world, encoding="utf-8")
    try:
        with pytest.raises(LoaderError, match="local_register"):
            load_world_magic(genre_yaml=GENRE_YAML, world_yaml=bad_path)
    finally:
        bad_path.unlink(missing_ok=True)


def test_narrator_register_composition_order_world_overrides_genre():
    """Per architect addendum 2026-04-29 §5.1: composition order is
    plugin-default → genre-override → world-override (last-writer-wins
    per field). World narrator_register beats genre narrator_register."""
    config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=WORLD_YAML)
    assert "Reach doesn't perform miracles" in config.narrator_register
    assert "Space opera magic is rare" not in config.narrator_register


def test_narrator_register_genre_overrides_plugin_default():
    """Genre narrator_register beats plugin's default register (when world
    declines to override). For this test, build a world fixture without a
    narrator_register and assert the genre value surfaces."""
    bare_world_yaml = (
        WORLD_YAML.read_text(encoding="utf-8")
        .split("narrator_register:")[0]
        .rstrip()
    )
    bare_path = FIXTURES / "_bare_register.yaml"
    bare_path.write_text(bare_world_yaml, encoding="utf-8")
    try:
        config = load_world_magic(genre_yaml=GENRE_YAML, world_yaml=bare_path)
        assert "Space opera magic is rare" in config.narrator_register
    finally:
        bare_path.unlink(missing_ok=True)


def test_loader_error_is_genre_error_subclass():
    """`LoaderError` integrates with the existing `GenreError` hierarchy so
    callers catching the genre exception family pick up magic-load failures."""
    from sidequest.genre.error import GenreError
    from sidequest.genre.magic_loader import LoaderError as InternalLoaderError

    assert issubclass(InternalLoaderError, GenreError)


def test_loader_symbols_exported_via_genre_package():
    """`from sidequest.genre import LoaderError, load_world_magic` works."""
    from sidequest.genre import LoaderError as PublicLoaderError
    from sidequest.genre import load_world_magic as public_load
    from sidequest.genre.magic_loader import LoaderError as InternalLoaderError
    from sidequest.genre.magic_loader import load_world_magic as internal_load

    assert PublicLoaderError is InternalLoaderError
    assert public_load is internal_load


def test_malformed_hard_limit_raises_loader_error():
    """A genre YAML with a hard_limit missing required `description` should
    surface as `LoaderError`, not raw `pydantic.ValidationError`."""
    bad_genre = GENRE_YAML.read_text(encoding="utf-8").replace(
        '  - id: no_resurrection\n    description: "Death is permanent. No one comes back."',
        "  - id: no_resurrection",  # description stripped
    )
    bad_path = FIXTURES / "_bad_hardlimit.yaml"
    bad_path.write_text(bad_genre, encoding="utf-8")
    try:
        with pytest.raises(LoaderError, match="hard_limits invalid"):
            load_world_magic(genre_yaml=bad_path, world_yaml=WORLD_YAML)
    finally:
        bad_path.unlink(missing_ok=True)


def test_narrator_register_falls_through_to_plugin_default_when_neither_overrides():
    """If neither world nor genre supplies narrator_register, the plugin's
    default register surfaces. Bare-bones genre + bare-bones world — the
    plugin descriptor's narrator_register should be the result.

    Implementer note: this test exercises the full three-layer fallback
    chain. Resolve before Phase 1 cut-point.
    """
    pytest.skip("fixture-authoring TODO — see implementer note")
