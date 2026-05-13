"""RED tests for Story 50-18 — scene-harness fixture hydrator.

Unit tests for ``sidequest.game.scene_harness.hydrate_fixture``: the YAML →
``GameSnapshot`` converter that backs ``POST /dev/scene/{name}`` per ADR-092.

The hydrator is currently absent (ADR-092 implementation-status: partial;
ADR-087 P0). These tests describe the contract Dev must satisfy in the
GREEN phase.

Hydrator contract (extracted from ADR-092 §Hydration rules and the four
canonical fixtures in ``scenarios/fixtures/``):

    from sidequest.game.scene_harness import (
        FixtureNotFoundError,
        FixtureValidationError,
        hydrate_fixture,
    )

    snapshot = hydrate_fixture(name="combat_test", fixtures_dir=Path(...))

* Returns a ``GameSnapshot`` with ``model_config = {"extra": "ignore"}``.
* Raises ``FixtureNotFoundError`` when ``{fixtures_dir}/{name}.yaml`` is missing.
* Raises ``FixtureValidationError`` on schema violations (missing ``genre``
  / ``world``, malformed ``character``, etc.) — never a bare ``yaml.YAMLError``
  or ``pydantic.ValidationError`` leaking past the module boundary.
* MUST use ``yaml.safe_load`` (lang-review rule #8 — never ``yaml.load`` on
  untrusted-input parsers; the harness is dev-gated, but the rule still binds).
* MUST NOT silently default missing ``genre`` / ``world`` to empty strings
  (CLAUDE.md "No Silent Fallbacks").
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Canonical fixtures live at ``orc-quest/scenarios/fixtures/`` — one level up
# from the server tree. The tests resolve relative to this file so they keep
# working when the suite is invoked from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_FIXTURES_DIR = REPO_ROOT / "scenarios" / "fixtures"


# ── Module surface ──────────────────────────────────────────────────────────


def test_module_exports_hydrate_fixture() -> None:
    """The hydrator is importable from a stable public path.

    ADR-092 implementation table assigns the hydrator to ``sidequest-server``.
    Tests, the dev-gated route, and any future companion tools must import
    from one canonical location — drift between callers is a wiring bug.
    """
    import sidequest.game.scene_harness as scene_harness

    assert hasattr(scene_harness, "hydrate_fixture"), (
        "scene_harness module must export hydrate_fixture()"
    )
    assert callable(scene_harness.hydrate_fixture)


def test_module_exports_error_types() -> None:
    """Distinct exception types let callers (the HTTP route) map errors to
    404 vs 422 without inspecting message strings."""
    from sidequest.game.scene_harness import (
        FixtureNotFoundError,
        FixtureValidationError,
    )

    # Both are real Exception subclasses with distinct identities.
    assert issubclass(FixtureNotFoundError, Exception)
    assert issubclass(FixtureValidationError, Exception)
    assert FixtureNotFoundError is not FixtureValidationError


# ── Happy path: canonical fixtures hydrate ──────────────────────────────────


@pytest.mark.parametrize(
    "fixture_name",
    ["combat_test", "dogfight", "negotiation", "poker"],
)
def test_canonical_fixture_hydrates_without_error(fixture_name: str) -> None:
    """Every canonical fixture in scenarios/fixtures/ must hydrate cleanly.

    These four were authored against ADR-069 and are the regression set
    ADR-092 §Implementation must keep green.
    """
    from sidequest.game.scene_harness import hydrate_fixture
    from sidequest.game.session import GameSnapshot

    snapshot = hydrate_fixture(name=fixture_name, fixtures_dir=CANONICAL_FIXTURES_DIR)

    assert isinstance(snapshot, GameSnapshot), (
        f"hydrate_fixture({fixture_name!r}) must return GameSnapshot, "
        f"got {type(snapshot).__name__}"
    )


def test_combat_test_fixture_populates_genre_and_world() -> None:
    """combat_test.yaml carries genre=mutant_wasteland, world=flickering_reach.

    The hydrator must preserve top-level identity fields verbatim — the
    slug-keyed connect flow keys on ``genre_slug`` + ``world_slug``, so any
    silent rename breaks the entire downstream wire-up.
    """
    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="combat_test", fixtures_dir=CANONICAL_FIXTURES_DIR)

    assert snapshot.genre_slug == "mutant_wasteland"
    assert snapshot.world_slug == "flickering_reach"


def test_combat_test_fixture_populates_first_character() -> None:
    """``character:`` block hydrates into ``snapshot.characters[0]`` per
    ADR-069 §Hydration rule 2 (inherited unchanged by ADR-092)."""
    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="combat_test", fixtures_dir=CANONICAL_FIXTURES_DIR)

    assert len(snapshot.characters) >= 1, "fixture defined a character block"
    pc = snapshot.characters[0]
    # ``Character.name`` is a method (Combatant Protocol); the data lives at
    # ``.core.name``. The fixture YAML's flat ``name:`` field un-flattens
    # into ``Character.core.name`` per the post-port shape.
    assert pc.core.name == "Skar", (
        f"expected Skar from combat_test.yaml, got {pc.core.name!r}"
    )


def test_combat_test_fixture_populates_npc_list() -> None:
    """``npcs:`` block hydrates into ``snapshot.npcs`` per ADR-069 rule 4 —
    name, role, disposition each preserved."""
    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="combat_test", fixtures_dir=CANONICAL_FIXTURES_DIR)

    # combat_test.yaml defines exactly one NPC: Rust Jaw (hostile, disposition -15).
    # Like Character, Npc nests CreatureCore under ``.core``.
    rust_jaw = next((n for n in snapshot.npcs if n.core.name == "Rust Jaw"), None)
    assert rust_jaw is not None, "Rust Jaw must hydrate from combat_test.yaml"


def test_minimal_fixture_with_only_genre_and_world(tmp_path: Path) -> None:
    """Unspecified fields use GameSnapshot defaults (ADR-069 rule 8).

    A fixture with only ``genre:`` and ``world:`` should hydrate; everything
    else is field-defaulted by pydantic. This is the cheapest possible
    fixture and the baseline for "minimal repro" debugging.
    """
    fixture = tmp_path / "minimal.yaml"
    fixture.write_text(
        "genre: caverns_and_claudes\n"
        "world: default\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="minimal", fixtures_dir=tmp_path)

    assert snapshot.genre_slug == "caverns_and_claudes"
    assert snapshot.world_slug == "default"
    # Defaults: empty list, empty list, empty string.
    assert snapshot.characters == []
    assert snapshot.npcs == []


def test_unknown_top_level_fields_are_ignored(tmp_path: Path) -> None:
    """GameSnapshot uses ``extra="ignore"`` — unknown YAML keys must NOT raise.

    Forward-compat: fixtures may carry comments or experimental keys that
    don't map to current GameSnapshot fields. Per ADR-092, this is the
    pydantic ``extra="ignore"`` discipline.
    """
    fixture = tmp_path / "extra_fields.yaml"
    fixture.write_text(
        "genre: caverns_and_claudes\n"
        "world: default\n"
        "experimental_unknown_field: 42\n"
        "_internal_note: this is a comment-like key\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="extra_fields", fixtures_dir=tmp_path)
    # The unknown fields are not present on GameSnapshot — pydantic dropped them.
    assert not hasattr(snapshot, "experimental_unknown_field")
    assert snapshot.genre_slug == "caverns_and_claudes"


# ── Error paths: distinguish 404 (missing) from 422 (invalid) ───────────────


def test_missing_fixture_raises_FixtureNotFoundError(tmp_path: Path) -> None:
    """A fixture filename that doesn't exist on disk must raise the
    not-found exception — not a generic ``FileNotFoundError`` or, worse,
    a silent ``GameSnapshot()`` with default fields (ADR-092 §Decision
    point 5: "Failure is loud")."""
    from sidequest.game.scene_harness import FixtureNotFoundError, hydrate_fixture

    with pytest.raises(FixtureNotFoundError) as exc_info:
        hydrate_fixture(name="does_not_exist", fixtures_dir=tmp_path)

    # The error message must surface the path so the dev knows where to look.
    msg = str(exc_info.value)
    assert "does_not_exist" in msg, (
        f"FixtureNotFoundError must name the missing fixture; got: {msg!r}"
    )


def test_missing_genre_raises_FixtureValidationError(tmp_path: Path) -> None:
    """ADR-092 §Hydration rule 1: top-level ``genre:`` is REQUIRED.

    A silent default to ``""`` would break slug minting downstream — the
    request must fail loudly at validation time, not silently produce a
    save file with an empty genre that then 500s on connect.
    """
    fixture = tmp_path / "no_genre.yaml"
    fixture.write_text("world: default\n", encoding="utf-8")

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError) as exc_info:
        hydrate_fixture(name="no_genre", fixtures_dir=tmp_path)

    # Field-level detail per ADR-092 §Decision point 5.
    assert "genre" in str(exc_info.value).lower()


def test_missing_world_raises_FixtureValidationError(tmp_path: Path) -> None:
    """ADR-092 §Hydration rule 1: top-level ``world:`` is REQUIRED."""
    fixture = tmp_path / "no_world.yaml"
    fixture.write_text("genre: caverns_and_claudes\n", encoding="utf-8")

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError) as exc_info:
        hydrate_fixture(name="no_world", fixtures_dir=tmp_path)

    assert "world" in str(exc_info.value).lower()


def test_empty_genre_string_raises_FixtureValidationError(tmp_path: Path) -> None:
    """``genre: ""`` is the silent-fallback trap: pydantic accepts the empty
    string for ``genre_slug: str = ""``, but the slug generator downstream
    will produce a malformed slug. Hydrator must reject before the snapshot
    ever leaves the boundary.
    """
    fixture = tmp_path / "empty_genre.yaml"
    fixture.write_text(
        'genre: ""\n'
        "world: default\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="empty_genre", fixtures_dir=tmp_path)


def test_malformed_yaml_raises_FixtureValidationError(tmp_path: Path) -> None:
    """Tab-indented YAML / unbalanced brackets / invalid syntax must surface
    as a structured ``FixtureValidationError`` — never a raw ``yaml.YAMLError``
    that leaks the parser stack trace to the HTTP layer."""
    fixture = tmp_path / "garbage.yaml"
    # Unterminated single-quote — a yaml.scanner.ScannerError producer.
    fixture.write_text("genre: 'unterminated\nworld: default\n", encoding="utf-8")

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="garbage", fixtures_dir=tmp_path)


# ── Security & rule-driven tests (lang-review rules #6, #8, #11) ────────────


def test_hydrator_uses_yaml_safe_load_not_yaml_load(tmp_path: Path) -> None:
    """lang-review rule #8: unsafe deserialization.

    ``yaml.load`` without ``Loader=SafeLoader`` allows arbitrary Python
    object instantiation via ``!!python/object/apply:`` tags. The hydrator
    MUST use ``yaml.safe_load`` (or an explicit ``SafeLoader``).

    Test strategy: write a fixture that exploits ``yaml.load`` semantics
    (a ``!!python/object/apply:os.system [['echo "PWN"']]`` payload). If
    the hydrator uses ``yaml.safe_load``, parsing raises and we surface
    ``FixtureValidationError``. If it uses ``yaml.load``, the payload
    constructs (and may even execute) — either way the snapshot type is
    wrong and the test fails.
    """
    fixture = tmp_path / "exploit.yaml"
    # ``!!python/name:os.system`` is the simplest payload that yaml.safe_load
    # rejects and yaml.load happily instantiates as a callable.
    fixture.write_text(
        "genre: !!python/name:os.system\n"
        "world: default\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="exploit", fixtures_dir=tmp_path)


def test_fixture_name_is_validated_against_path_traversal(tmp_path: Path) -> None:
    """lang-review rule #11: input validation at boundaries.

    ``name`` is reflected into a filesystem path. ``../../etc/passwd`` or
    an absolute path component MUST be rejected before any I/O, mapping
    to ``FixtureNotFoundError`` (or ``FixtureValidationError``) rather
    than silently traversing out of ``fixtures_dir``.
    """
    from sidequest.game.scene_harness import (
        FixtureNotFoundError,
        FixtureValidationError,
        hydrate_fixture,
    )

    # Either error type is acceptable — both are loud failures, neither
    # results in a traversed read.
    with pytest.raises((FixtureNotFoundError, FixtureValidationError)):
        hydrate_fixture(name="../etc/passwd", fixtures_dir=tmp_path)
