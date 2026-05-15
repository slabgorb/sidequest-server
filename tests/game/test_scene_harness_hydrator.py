"""Tests for Stories 50-18 and 50-19 — scene-harness fixture hydrator.

Unit tests for ``sidequest.game.scene_harness.hydrate_fixture``: the YAML →
``GameSnapshot`` converter that backs ``POST /dev/scene/{name}`` per ADR-092.

Layout:
* Lines 80-329 — Story 50-18 RED tests (hydrator contract, error mapping,
  yaml.safe_load discipline, path-traversal guard).
* Lines 332+ — Story 50-19 tests (extend ``_hydrate_character()`` to
  hydrate ``Character.known_facts`` from a ``known_facts:`` YAML block).

The 50-18 contract was the original spec for this file; the 50-19 cases
extend it for the known_facts hydration path.

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


# ── Story 50-19: known_facts hydration (ADR-092 follow-on) ──────────────────
#
# RED tests for the known_facts extension of _hydrate_character(). Each entry
# under character.known_facts must construct a KnownFact with confidence in
# Literal["Certain", "Suspected", "Rumored", "Discovered"] (post-50-17 enum
# promotion). The fixture authoring contract:
#
#     character:
#       name: Wren
#       ...
#       known_facts:
#         - content: "..."
#           confidence: "Certain"
#         - content: "..."
#           confidence: "Suspected"
#
# Defaults from KnownFact carry through when fields are omitted; the model
# itself uses ``extra="forbid"`` so a typo in the YAML key surfaces loudly.


def _write_character_fixture(tmp_path: Path, name: str, known_facts_yaml: str) -> None:
    """Helper: write a minimal fixture with a character.known_facts block.

    Keeps test bodies focused on the assertion, not the YAML scaffolding.
    """
    fixture = tmp_path / f"{name}.yaml"
    fixture.write_text(
        "genre: caverns_and_claudes\n"
        "world: default\n"
        "character:\n"
        "  name: Wren\n"
        "  description: A scout\n"
        "  personality: cautious\n"
        "  backstory: scouted these tunnels before\n"
        "  char_class: thief\n"
        "  race: human\n"
        f"  known_facts:\n{known_facts_yaml}",
        encoding="utf-8",
    )


def test_character_known_facts_block_hydrates(tmp_path: Path) -> None:
    """AC#1, AC#4: a single known_facts entry projects to character.known_facts.

    The base hydrator already handles every other character field; this is
    the new wiring story 50-19 must add.
    """
    _write_character_fixture(
        tmp_path,
        "single_fact",
        '    - content: "The goblin speaks broken common"\n'
        '      confidence: "Certain"\n',
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="single_fact", fixtures_dir=tmp_path)

    pc = snapshot.characters[0]
    assert len(pc.known_facts) == 1, (
        f"expected one KnownFact hydrated from known_facts: block, "
        f"got {len(pc.known_facts)}"
    )
    fact = pc.known_facts[0]
    assert fact.content == "The goblin speaks broken common"
    assert fact.confidence == "Certain"


@pytest.mark.parametrize(
    "confidence",
    ["Certain", "Suspected", "Rumored", "Discovered"],
)
def test_known_facts_all_four_confidence_tiers(
    tmp_path: Path, confidence: str
) -> None:
    """AC#5, AC#6: every confidence tier in the Literal hydrates verbatim.

    Parametrized so a regression on one tier doesn't masquerade as a
    "test passed" because the suite only happened to hit "Certain".
    """
    _write_character_fixture(
        tmp_path,
        f"tier_{confidence.lower()}",
        f'    - content: "fact about {confidence.lower()}"\n'
        f'      confidence: "{confidence}"\n',
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name=f"tier_{confidence.lower()}", fixtures_dir=tmp_path)

    pc = snapshot.characters[0]
    assert pc.known_facts[0].confidence == confidence, (
        f"confidence mismatch — fixture wrote {confidence!r}, "
        f"hydrator produced {pc.known_facts[0].confidence!r}"
    )


def test_known_facts_mixed_confidence_fixture(tmp_path: Path) -> None:
    """AC#5: a fixture with four facts spanning all tiers hydrates in order.

    The session-file canonical example — verifies list ordering survives
    YAML → KnownFact projection (no dict-key reordering or set coercion).
    """
    _write_character_fixture(
        tmp_path,
        "mixed_confidence",
        '    - content: "The goblin speaks broken common"\n'
        '      confidence: "Certain"\n'
        '    - content: "A larger creature lurks deeper"\n'
        '      confidence: "Suspected"\n'
        '    - content: "Spiked weapons are common"\n'
        '      confidence: "Rumored"\n'
        '    - content: "There is a hidden exit"\n'
        '      confidence: "Discovered"\n',
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="mixed_confidence", fixtures_dir=tmp_path)

    pc = snapshot.characters[0]
    confidences = [f.confidence for f in pc.known_facts]
    assert confidences == ["Certain", "Suspected", "Rumored", "Discovered"], (
        f"YAML list order must survive hydration; got {confidences!r}"
    )
    # Spot-check content survives too — paranoia against a swap bug where
    # confidences land correctly but content is paired off-by-one.
    assert pc.known_facts[3].content == "There is a hidden exit"


def test_invalid_confidence_raises_FixtureValidationError(tmp_path: Path) -> None:
    """AC#3: a confidence string outside the Literal must raise 422, not 500.

    Post-50-17 pydantic owns this validation; the hydrator's job is just
    to wrap pydantic's ``ValidationError`` as ``FixtureValidationError``
    (the existing pattern for character.* and npcs.*).
    """
    _write_character_fixture(
        tmp_path,
        "bad_confidence",
        '    - content: "this fact has a typo"\n'
        '      confidence: "Bogus"\n',
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError) as exc_info:
        hydrate_fixture(name="bad_confidence", fixtures_dir=tmp_path)

    # The error message should point at the offending field so the dev
    # knows what to fix without re-running the hydrator in a debugger.
    msg = str(exc_info.value).lower()
    assert "confidence" in msg or "bogus" in msg or "known_facts" in msg, (
        f"FixtureValidationError must name confidence/known_facts in its message; "
        f"got: {msg!r}"
    )


def test_legacy_confirmed_confidence_is_rejected(tmp_path: Path) -> None:
    """50-17 regression seam: the pre-promotion value 'confirmed' must NOT
    silently coerce to 'Certain'.

    The KnownFact docstring spells this out:
        "The pre-50-17 legacy value 'confirmed' is rejected."

    If hydrator (or pydantic) ever started accepting it again, every
    save-file written against the new enum would drift toward the old
    string and the journal UI confidence-prop chain would corrupt.
    """
    _write_character_fixture(
        tmp_path,
        "legacy_confirmed",
        '    - content: "ancient fact written under old schema"\n'
        '      confidence: "confirmed"\n',
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="legacy_confirmed", fixtures_dir=tmp_path)


def test_hydrated_known_facts_have_accusation_weight(tmp_path: Path) -> None:
    """AC#7 (adapted): every hydrated confidence value is a valid key in
    the accusation weight lookup table.

    Deviation logged: SM's session referenced ``AccusationEvaluator._confidence_weight()``,
    which doesn't exist as a method. The actual weight lookup is the
    module-level dict ``sidequest.game.accusation._CONFIDENCE_WEIGHTS``
    indexed in :meth:`AccusationEvaluator.evaluate` (line 184). The
    integration probe is mechanically equivalent: a KeyError here would
    mean the hydrated confidence string fell outside the supported set.
    """
    _write_character_fixture(
        tmp_path,
        "weights",
        '    - content: "fact A"\n'
        '      confidence: "Certain"\n'
        '    - content: "fact B"\n'
        '      confidence: "Suspected"\n'
        '    - content: "fact C"\n'
        '      confidence: "Rumored"\n'
        '    - content: "fact D"\n'
        '      confidence: "Discovered"\n',
    )

    from sidequest.game.accusation import _CONFIDENCE_WEIGHTS
    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="weights", fixtures_dir=tmp_path)
    pc = snapshot.characters[0]

    # Every hydrated fact must produce a weight without KeyError.
    weights = [_CONFIDENCE_WEIGHTS[f.confidence] for f in pc.known_facts]
    assert weights == [2.0, 1.0, 0.5, 1.5], (
        f"confidence weights drifted from accusation.py contract; got {weights!r}"
    )


def test_missing_known_facts_defaults_to_empty_list(tmp_path: Path) -> None:
    """AC#8 (backward compat): a character with no ``known_facts:`` key
    still hydrates, and ``Character.known_facts`` is the empty list.

    This is the regression guard against the 50-19 implementation
    accidentally requiring the new key. Existing canonical fixtures
    (combat_test, dogfight, negotiation, poker) do not declare
    known_facts and must continue to load.
    """
    fixture = tmp_path / "no_facts.yaml"
    fixture.write_text(
        "genre: caverns_and_claudes\n"
        "world: default\n"
        "character:\n"
        "  name: Wren\n"
        "  description: A scout\n"
        "  personality: cautious\n"
        "  backstory: scouted these tunnels before\n"
        "  char_class: thief\n"
        "  race: human\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import hydrate_fixture

    snapshot = hydrate_fixture(name="no_facts", fixtures_dir=tmp_path)
    pc = snapshot.characters[0]
    assert pc.known_facts == [], (
        f"omitting known_facts: should yield empty list, got {pc.known_facts!r}"
    )


def test_canonical_fixtures_still_hydrate_with_known_facts_implementation() -> None:
    """AC#8: the canonical fixtures shipped pre-50-19 must continue to
    hydrate after the known_facts code path is added.

    Wiring-test (CLAUDE.md "Every Test Suite Needs a Wiring Test"):
    proves 50-19's hydrator change didn't break the regression set.

    NB: the 50-18 tests at the top of this file reference legacy names
    (combat_test, dogfight, negotiation, poker) that do not exist in
    ``scenarios/fixtures/`` — see TEA Delivery Findings. This test uses
    the real filenames so its red/green status reflects ONLY the
    known_facts change.
    """
    from sidequest.game.scene_harness import hydrate_fixture

    real_fixtures = (
        "combat_brawl_wasteland",
        "combat_dogfight_space",
        "social_negotiation_tea",
        "social_poker_wasteland",
    )
    for fixture_name in real_fixtures:
        snapshot = hydrate_fixture(
            name=fixture_name, fixtures_dir=CANONICAL_FIXTURES_DIR
        )
        # Each canonical PC either has no known_facts block or it's a
        # well-formed empty list. Either way: not None, no exceptions.
        for pc in snapshot.characters:
            assert isinstance(pc.known_facts, list), (
                f"{fixture_name}: pc.known_facts must be a list, "
                f"got {type(pc.known_facts).__name__}"
            )


def test_known_facts_entry_uses_KnownFact_defaults_when_fields_omitted(
    tmp_path: Path,
) -> None:
    """AC#2: a known_facts entry with only ``content`` + ``confidence``
    inherits KnownFact defaults (source="GameEvent", learned_turn=0,
    auto-minted fact_id, category=FactCategory.Lore).

    Verifies the hydrator forwards the entry to the pydantic constructor
    rather than re-implementing defaults locally (which would drift over
    time).
    """
    _write_character_fixture(
        tmp_path,
        "minimal_fact",
        '    - content: "minimal entry"\n'
        '      confidence: "Suspected"\n',
    )

    from sidequest.game.character import KnownFact
    from sidequest.game.scene_harness import hydrate_fixture
    from sidequest.protocol.models import FactCategory

    snapshot = hydrate_fixture(name="minimal_fact", fixtures_dir=tmp_path)
    fact = snapshot.characters[0].known_facts[0]

    assert isinstance(fact, KnownFact), (
        f"hydrated entry must be a KnownFact instance, got {type(fact).__name__}"
    )
    assert fact.source == "GameEvent"
    assert fact.learned_turn == 0
    assert fact.category == FactCategory.Lore
    # fact_id is auto-minted (uuid4 hex) — non-empty and not the literal default.
    assert fact.fact_id and len(fact.fact_id) >= 8


def test_known_facts_not_a_list_raises_FixtureValidationError(tmp_path: Path) -> None:
    """ADR-092 §"Failure is loud" + lang-review rule #1 (silent exception
    swallowing): if a fixture sets ``known_facts:`` to a mapping or scalar
    instead of a list, the hydrator MUST surface a structured error rather
    than silently skip the block.

    The base hydrator's sibling pattern (``inventory`` and ``statuses``)
    silently ignores wrong shapes, which would mask a fixture typo. For
    known_facts — a stateful, save-bearing field — silent skip is a
    correctness hazard. Loud is correct.
    """
    fixture = tmp_path / "bad_facts_shape.yaml"
    fixture.write_text(
        "genre: caverns_and_claudes\n"
        "world: default\n"
        "character:\n"
        "  name: Wren\n"
        "  description: A scout\n"
        "  personality: cautious\n"
        "  backstory: scouted these tunnels before\n"
        "  char_class: thief\n"
        "  race: human\n"
        "  known_facts:\n"
        "    not_a_list: true\n",
        encoding="utf-8",
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="bad_facts_shape", fixtures_dir=tmp_path)


def test_known_facts_extra_field_rejected_by_pydantic(tmp_path: Path) -> None:
    """KnownFact has ``model_config = {"extra": "forbid"}`` — a typo'd key
    in the fixture (e.g., ``confidance: Certain``) must surface as a
    FixtureValidationError, not silently drop the value.

    This guards the model's extra=forbid contract through the hydrator.
    """
    _write_character_fixture(
        tmp_path,
        "extra_field",
        '    - content: "fact with typo"\n'
        '      confidance: "Certain"\n',  # typo: confidance vs confidence
    )

    from sidequest.game.scene_harness import FixtureValidationError, hydrate_fixture

    with pytest.raises(FixtureValidationError):
        hydrate_fixture(name="extra_field", fixtures_dir=tmp_path)
