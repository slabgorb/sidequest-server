"""Regression tests for playtest 2026-04-30 #9 — magic_state resume backfill.

Pre-fix, ``init_magic_state_for_session`` was wired into chargen but
NOT into either of the resume paths in ``ConnectHandler``. A save
created before the chargen hook landed (or via any code path that
skipped chargen) resumed with ``snapshot.magic_state = None``, the
``magic_working`` pipeline silently no-op'd, and the LedgerPanel never
surfaced bars — exactly the half-wired feature CLAUDE.md "Verify
Wiring, Not Just Existence" forbids.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sidequest.game.character import Character, CreatureCore
from sidequest.game.session import GameSnapshot
from sidequest.handlers.connect import _backfill_magic_state_on_resume

CONTENT_ROOT = (
    Path(__file__).resolve().parents[2].parent
    / "sidequest-content"
    / "genre_packs"
)


def _coyote_reach_pack_with_character() -> tuple[GameSnapshot, SimpleNamespace]:
    pack_dir = CONTENT_ROOT / "space_opera"
    if not (pack_dir / "magic.yaml").is_file():
        pytest.skip("space_opera magic.yaml not present in this checkout")

    snap = GameSnapshot(genre_slug="space_opera", world_slug="coyote_reach")
    snap.characters.append(
        Character(
            core=CreatureCore(
                name="Hokulea",
                description="Engineer.",
                personality="Quiet.",
            ),
            backstory="Voidborn.",
            char_class="engineer",
            race="human",
            pronouns="they/them",
        )
    )
    # ConnectHandler reads `genre_pack.source_dir` — mirror just enough
    # of that surface to exercise the helper.
    pack_stub = SimpleNamespace(source_dir=pack_dir)
    return snap, pack_stub


def test_backfill_runs_when_magic_state_is_none_and_world_has_magic() -> None:
    """The motivating scenario: save predates chargen's magic hook. On
    resume, helper detects None + magic.yaml present + a seated PC,
    calls ``init_magic_state_for_session``, and snapshot.magic_state is
    populated for the LedgerPanel.
    """
    snap, pack = _coyote_reach_pack_with_character()
    assert snap.magic_state is None

    _backfill_magic_state_on_resume(
        snapshot=snap, genre_pack=pack, world_slug="coyote_reach"
    )

    assert snap.magic_state is not None
    assert snap.magic_state.config.world_slug == "coyote_reach"
    char_keys = [
        k for k in snap.magic_state.ledger if k.startswith("character|Hokulea|")
    ]
    assert char_keys, (
        "expected per-character bars after backfill; got "
        f"{list(snap.magic_state.ledger.keys())}"
    )


def test_backfill_no_op_when_magic_state_already_populated() -> None:
    """Resume from a post-init save: magic_state exists. Helper must
    not overwrite it (would clobber the per-character ledger debits the
    save was tracking).
    """
    snap, pack = _coyote_reach_pack_with_character()
    # Seed magic_state via the same helper to mirror "the save already
    # had it" rather than constructing one by hand.
    from sidequest.server.magic_init import init_magic_state_for_session

    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack.source_dir,
        world_slug="coyote_reach",
        character_id="Hokulea",
    )
    sentinel = snap.magic_state
    assert sentinel is not None

    _backfill_magic_state_on_resume(
        snapshot=snap, genre_pack=pack, world_slug="coyote_reach"
    )

    assert snap.magic_state is sentinel, (
        "backfill must NOT replace an already-populated magic_state"
    )


def test_backfill_no_op_when_world_has_no_magic(tmp_path: Path) -> None:
    """A genre/world without magic.yaml is the common case (most
    settings). Helper must leave magic_state=None — failing-loud here
    would crash every non-magic resume.
    """
    snap = GameSnapshot(genre_slug="invented_genre", world_slug="invented_world")
    snap.characters.append(
        Character(
            core=CreatureCore(name="X", description=".", personality="."),
            backstory=".", char_class="z", race="z", pronouns="z",
        )
    )
    # tmp_path has no magic.yaml — `init_magic_state_for_session` will
    # short-circuit on the missing file.
    pack_stub = SimpleNamespace(source_dir=tmp_path)
    _backfill_magic_state_on_resume(
        snapshot=snap, genre_pack=pack_stub, world_slug="invented_world"
    )
    assert snap.magic_state is None


def test_backfill_no_op_when_no_seated_character() -> None:
    """A snapshot with no characters yet (chargen-mid-resume) has
    nothing to key the ledger by. Helper must skip — chargen will
    re-call init_magic_state_for_session itself when the character
    materialises.
    """
    pack_dir = CONTENT_ROOT / "space_opera"
    if not (pack_dir / "magic.yaml").is_file():
        pytest.skip("space_opera magic.yaml not present")
    snap = GameSnapshot(genre_slug="space_opera", world_slug="coyote_reach")
    pack_stub = SimpleNamespace(source_dir=pack_dir)

    _backfill_magic_state_on_resume(
        snapshot=snap, genre_pack=pack_stub, world_slug="coyote_reach"
    )

    assert snap.magic_state is None


def test_backfill_no_op_when_pack_source_dir_missing() -> None:
    """Genre packs constructed from a non-disk source (test fixtures,
    cached objects) have no source_dir. The helper must skip rather
    than crashing — those resumes can't load YAML in the first place.
    """
    snap = GameSnapshot(genre_slug="space_opera", world_slug="coyote_reach")
    snap.characters.append(
        Character(
            core=CreatureCore(name="X", description=".", personality="."),
            backstory=".", char_class="z", race="z", pronouns="z",
        )
    )
    pack_stub = SimpleNamespace(source_dir=None)
    _backfill_magic_state_on_resume(
        snapshot=snap, genre_pack=pack_stub, world_slug="coyote_reach"
    )
    assert snap.magic_state is None
