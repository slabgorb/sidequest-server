"""Scene-harness fixture hydrator (ADR-092 §Implementation).

Reads a YAML fixture from ``scenarios/fixtures/{name}.yaml`` and hydrates
it into a :class:`GameSnapshot` the existing ``SqliteStore`` can persist.
The dev-gated HTTP route in :mod:`sidequest.server.scene_harness_router`
wraps this hydrator; this module owns no I/O beyond the single fixture
read.

Two distinct error types let the HTTP layer map failures to 404 vs 422:

* :exc:`FixtureNotFoundError` — the fixture file does not exist in
  ``fixtures_dir``. HTTP layer surfaces as 404.
* :exc:`FixtureValidationError` — the fixture exists but its contents
  are invalid (missing ``genre``/``world``, malformed YAML, unsafe YAML
  payload, field-level validation rejection). HTTP layer surfaces as 422.

The hydrator inherits ADR-069's YAML schema and ADR-092's "Failure is
loud" discipline: no silent defaults for required identity fields, no
``yaml.load`` on untrusted input, no path traversal out of
``fixtures_dir``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sidequest.game.character import Character, KnownFact
from sidequest.game.creature_core import CreatureCore
from sidequest.game.session import GameSnapshot, Npc

logger = logging.getLogger(__name__)


class FixtureNotFoundError(Exception):
    """The requested fixture YAML does not exist at the resolved path."""


class FixtureValidationError(Exception):
    """The fixture exists but its contents fail validation.

    Wraps ``yaml.YAMLError`` and ``pydantic.ValidationError`` so the
    HTTP layer never sees parser-internal exception types.
    """


# ``fixture_name`` must look like a safe filename stem: lowercase
# alphanumerics, underscores, hyphens. Rejects path traversal
# (``../``), absolute paths (``/foo``), separators, NUL bytes, and
# the empty string.
_FIXTURE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def hydrate_fixture(*, name: str, fixtures_dir: Path) -> GameSnapshot:
    """Load and hydrate ``{fixtures_dir}/{name}.yaml`` into a GameSnapshot.

    :param name: Fixture stem (no extension). Must match
        ``[A-Za-z0-9][A-Za-z0-9_-]*`` — rejects path traversal and
        absolute paths before any I/O.
    :param fixtures_dir: Directory containing fixture YAMLs. Production
        wiring points at ``orc-quest/scenarios/fixtures/``.
    :raises FixtureNotFoundError: ``name`` is invalid or the resolved
        file does not exist.
    :raises FixtureValidationError: YAML parse error or schema violation.
    """
    if not _FIXTURE_NAME_RE.match(name):
        # Path-traversal guard. Mapping to FixtureNotFoundError keeps
        # the HTTP 404 contract uniform — never reveals whether a
        # traversal target *would* have existed.
        raise FixtureNotFoundError(
            f"fixture name {name!r} is invalid — must match [A-Za-z0-9][A-Za-z0-9_-]*"
        )

    fixture_path = (fixtures_dir / f"{name}.yaml").resolve()
    fixtures_dir_resolved = fixtures_dir.resolve()
    # Belt + suspenders: even if the regex above somehow lets a
    # traversal slip through (it should not), the resolved path must
    # live under ``fixtures_dir``. Anything else → not found.
    if not str(fixture_path).startswith(str(fixtures_dir_resolved)):
        raise FixtureNotFoundError(
            f"fixture {name!r} resolves outside fixtures_dir"
        )

    if not fixture_path.is_file():
        raise FixtureNotFoundError(
            f"fixture {name!r} not found at {fixture_path!s}"
        )

    try:
        raw_text = fixture_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureValidationError(
            f"failed to read fixture {name!r} at {fixture_path!s}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        # ``yaml.safe_load`` raises subclasses of YAMLError on both
        # parse errors AND on the ``!!python/object``-style payloads
        # that ``yaml.load`` would have happily instantiated. The
        # security-test path lands here.
        logger.warning("scene_harness.yaml_parse_error name=%s err=%s", name, exc)
        raise FixtureValidationError(
            f"fixture {name!r}: YAML parse error — {exc}"
        ) from exc

    if data is None:
        raise FixtureValidationError(
            f"fixture {name!r} is empty"
        )
    if not isinstance(data, dict):
        raise FixtureValidationError(
            f"fixture {name!r}: top level must be a YAML mapping, got {type(data).__name__}"
        )

    genre = data.get("genre")
    world = data.get("world")
    if not isinstance(genre, str) or not genre.strip():
        raise FixtureValidationError(
            f"fixture {name!r}: required field 'genre' is missing or empty"
        )
    if not isinstance(world, str) or not world.strip():
        raise FixtureValidationError(
            f"fixture {name!r}: required field 'world' is missing or empty"
        )

    snapshot_kwargs: dict[str, Any] = {
        "genre_slug": genre,
        "world_slug": world,
    }

    # Optional location (current_region) — fixtures use a free-text place
    # name. Wave 2B per-PC character_locations isn't wired here; the
    # narrator prompt builder consults current_region as a fallback.
    location = data.get("location")
    if isinstance(location, str) and location:
        snapshot_kwargs["current_region"] = location

    # Optional turn counter — combat_test sets turn=3 so the dispatcher
    # doesn't think it's turn 1.
    turn = data.get("turn")
    if isinstance(turn, int) and turn > 0:
        from sidequest.game.session import TurnManager

        snapshot_kwargs["turn_manager"] = TurnManager(interaction=turn)

    # Hydrate PCs (story 50-23, ADR-092 follow-on).
    #
    # Two mutually-exclusive shapes are supported:
    #   character:        — legacy singular form (ADR-069 §Hydration rule 2),
    #                       lands at ``characters[0]``.
    #   characters: [...] — multi-PC list, each entry the same shape as the
    #                       legacy singular block; projects to
    #                       ``characters[N]`` in fixture-declared order.
    #
    # Both blocks present is a fixture authoring bug — fail loudly per
    # CLAUDE.md "No Silent Fallbacks" rather than silently pick one.
    singular_character = data.get("character")
    characters_list = data.get("characters")

    if singular_character is not None and characters_list is not None:
        raise FixtureValidationError(
            f"fixture {name!r}: cannot declare both 'character' and 'characters' "
            "blocks — pick one (legacy singular form or multi-PC list)"
        )

    if isinstance(singular_character, dict):
        try:
            snapshot_kwargs["characters"] = [_hydrate_character(singular_character)]
        except ValidationError as exc:
            raise FixtureValidationError(
                f"fixture {name!r}: character field validation failed — {exc}"
            ) from exc
    elif characters_list is not None:
        if not isinstance(characters_list, list):
            raise FixtureValidationError(
                f"fixture {name!r}: 'characters' must be a YAML list, "
                f"got {type(characters_list).__name__}"
            )
        hydrated: list[Character] = []
        for index, entry in enumerate(characters_list):
            if not isinstance(entry, dict):
                raise FixtureValidationError(
                    f"fixture {name!r}: characters[{index}] must be a YAML mapping, "
                    f"got {type(entry).__name__}"
                )
            try:
                hydrated.append(_hydrate_character(entry))
            except ValidationError as exc:
                raise FixtureValidationError(
                    f"fixture {name!r}: characters[{index}] validation failed — {exc}"
                ) from exc
            except FixtureValidationError as exc:
                # ``_hydrate_character()`` raises FixtureValidationError for
                # malformed ``known_facts`` shape (story 50-19). Re-raise
                # with the entry index so the fixture author knows which
                # PC to fix.
                raise FixtureValidationError(
                    f"fixture {name!r}: characters[{index}] — {exc}"
                ) from exc
        snapshot_kwargs["characters"] = hydrated

    # Hydrate NPC roster.
    npcs_data = data.get("npcs")
    if isinstance(npcs_data, list):
        try:
            snapshot_kwargs["npcs"] = [_hydrate_npc(n) for n in npcs_data if isinstance(n, dict)]
        except ValidationError as exc:
            raise FixtureValidationError(
                f"fixture {name!r}: npcs field validation failed — {exc}"
            ) from exc

    try:
        snapshot = GameSnapshot(**snapshot_kwargs)
    except ValidationError as exc:
        raise FixtureValidationError(
            f"fixture {name!r}: snapshot construction failed — {exc}"
        ) from exc

    return snapshot


# ── Helpers ─────────────────────────────────────────────────────────────────


def _hydrate_character(data: dict[str, Any]) -> Character:
    """Map the fixture's flat character shape into ``Character`` + nested ``core``.

    Fixture YAMLs follow the legacy Rust shape where CreatureCore fields
    (name/description/personality/level/hp/max_hp/ac/inventory/statuses)
    are flattened to the top level. The Python port nests CreatureCore
    under ``Character.core``; this helper un-flattens.

    Legacy ``hp``/``max_hp`` integers are mapped onto :class:`EdgePool`
    (current/max/base_max). ``ac`` has no current home in the Python
    Character shape and is dropped.

    The ``or ""`` patterns on required string fields keep pyright happy
    (``data.get(...)`` is ``str | None`` but the pydantic constructors
    require ``str``). Pydantic's non-blank field validators then reject
    the empty strings at construction time, so an omitted required
    field still surfaces as a ``ValidationError`` that ``hydrate_fixture``
    re-wraps as ``FixtureValidationError`` (HTTP 422).
    """
    core_kwargs: dict[str, Any] = {
        "name": data.get("name") or "",
        "description": data.get("description") or "",
        "personality": data.get("personality") or "",
        "level": int(data.get("level", 1)),
    }
    inv = data.get("inventory")
    if isinstance(inv, dict):
        core_kwargs["inventory"] = inv
    statuses = data.get("statuses")
    if isinstance(statuses, list):
        core_kwargs["statuses"] = statuses

    hp = data.get("hp")
    max_hp = data.get("max_hp")
    if isinstance(hp, int) and isinstance(max_hp, int):
        core_kwargs["edge"] = {"current": hp, "max": max_hp, "base_max": max_hp}

    core = CreatureCore(**core_kwargs)

    # Hydrate known_facts (story 50-19, ADR-092 follow-on).
    #
    # Unlike inventory/statuses, known_facts is save-bearing — a malformed
    # shape must fail loudly (FixtureValidationError → HTTP 422) rather
    # than silently skip the block. KnownFact's pydantic constructor owns
    # confidence-tier validation post-50-17 (the legacy "confirmed" value
    # is rejected by the Literal); we let ValidationError propagate up to
    # the caller's wrap-as-FixtureValidationError block.
    known_facts: list[KnownFact] = []
    raw_facts = data.get("known_facts")
    if raw_facts is not None:
        if not isinstance(raw_facts, list):
            raise FixtureValidationError(
                f"character.known_facts must be a YAML list, "
                f"got {type(raw_facts).__name__}"
            )
        for index, entry in enumerate(raw_facts):
            if not isinstance(entry, dict):
                raise FixtureValidationError(
                    f"character.known_facts[{index}] must be a YAML mapping, "
                    f"got {type(entry).__name__}"
                )
            # fact_id is the UI dedup key (JournalResponsePayload.fact_id);
            # a fixture-supplied id matching a real ScenarioClue.id would
            # silently suppress the legitimate discovery from the journal
            # UI. Always mint fresh — fixture authors do not need to control
            # this identifier, and we don't want them to.
            scrubbed = {k: v for k, v in entry.items() if k != "fact_id"}
            known_facts.append(KnownFact(**scrubbed))

    return Character(
        core=core,
        backstory=data.get("backstory") or "",
        narrative_state=data.get("narrative_state", ""),
        hooks=list(data.get("hooks") or []),
        char_class=data.get("char_class") or "",
        race=data.get("race") or "",
        pronouns=data.get("pronouns", ""),
        stats=dict(data.get("stats") or {}),
        known_facts=known_facts,
    )


def _hydrate_npc(data: dict[str, Any]) -> Npc:
    """Map the fixture's flat NPC shape into ``Npc`` + nested ``core``.

    Fixture shape (ADR-069):
        - name: str
        - role: str       (informational — folded into description/personality)
        - disposition: int (Disposition coerces from int)

    ``CreatureCore.description`` and ``.personality`` have non-blank
    validators; fixtures rarely set them for NPCs (the narrator fills
    in flavor as needed). We seed from ``role`` when present so the
    validators are satisfied without ginning up prose the narrator
    might contradict.
    """
    # ``name`` is required and pydantic catches missing/blank. ``description``
    # and ``personality`` defaults are LOAD-BEARING: canonical fixtures
    # (combat_test, dogfight) define NPCs with only name/role/disposition
    # and rely on the narrator to fill in flavor; without seeded values
    # CreatureCore's non-blank validators would reject every fixture NPC.
    role = data.get("role") or "fixture NPC"
    core = CreatureCore(
        name=data.get("name") or "",
        description=data.get("description") or f"NPC ({role})",
        personality=data.get("personality") or role,
    )
    npc_kwargs: dict[str, Any] = {"core": core}
    if "disposition" in data:
        npc_kwargs["disposition"] = data["disposition"]
    return Npc(**npc_kwargs)
