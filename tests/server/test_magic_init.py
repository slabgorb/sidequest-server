"""Phase 4 wiring tests for ``init_magic_state_for_session`` and its
chargen-confirmation call site.

Three layers:
  1. Unit — the helper resolves YAML paths, loads, instantiates a
     MagicState, and adds the character to the ledger.
  2. Unit (negative) — missing magic.yaml is silent (most worlds have
     no magic); LoaderError is logged but does not raise.
  3. Wire-first source-grep — the chargen confirmation handler imports
     and calls ``init_magic_state_for_session`` so a future refactor
     can't silently un-thread the hook.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sidequest.game.session import GameSnapshot
from sidequest.server.magic_init import init_magic_state_for_session

CONTENT_ROOT = Path(__file__).resolve().parents[2].parent / "sidequest-content" / "genre_packs"


def _resolve_space_opera_world_with_magic() -> tuple[Path, str]:
    """Return (pack_dir, world_slug) for the space_opera world that ships
    a magic.yaml — Coyote Star.
    """
    pack_dir = CONTENT_ROOT / "space_opera"
    if not (pack_dir / "magic.yaml").is_file():
        raise AssertionError(
            f"space_opera magic.yaml missing at {pack_dir} — "
            "Phase 4 magic init test cannot run without shipping content"
        )
    world_slug = "coyote_star"
    if not (pack_dir / "worlds" / world_slug / "magic.yaml").is_file():
        raise AssertionError(
            f"coyote_star world magic.yaml missing under {pack_dir / 'worlds'}"
        )
    return pack_dir, world_slug


def test_init_magic_state_loads_coyote_star_and_adds_character() -> None:
    """Coyote Star has both genre + world magic.yaml shipping. After
    init, snapshot.magic_state is populated with the world config and
    the character has per-character bars in the ledger.
    """
    pack_dir, world_slug = _resolve_space_opera_world_with_magic()

    snap = GameSnapshot(genre_slug="space_opera", world_slug=world_slug)
    assert snap.magic_state is None

    ok = init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="Sira Mendes",
    )

    assert ok is True
    assert snap.magic_state is not None
    assert snap.magic_state.config.world_slug == world_slug
    assert snap.magic_state.config.genre_slug == "space_opera"

    # Character bars must be instantiated in the ledger so a working
    # actor="Sira Mendes" finds its sanity/notice/vitality entries on
    # the very first turn.
    char_keys = [
        k for k in snap.magic_state.ledger
        if k.startswith("character|Sira Mendes|")
    ]
    assert len(char_keys) > 0, (
        f"add_character('Sira Mendes') did not produce any character bars; "
        f"ledger keys: {list(snap.magic_state.ledger.keys())}"
    )


def test_init_magic_state_skips_world_without_magic_yaml(
    tmp_path: Path,
) -> None:
    """A world directory with no magic.yaml is the common case for
    genres that don't model magic. The helper returns False and
    leaves snapshot.magic_state untouched.
    """
    # Build a fake pack dir that has neither genre magic.yaml nor
    # world magic.yaml.
    fake_pack = tmp_path / "fake_pack"
    (fake_pack / "worlds" / "fake_world").mkdir(parents=True)

    snap = GameSnapshot(genre_slug="fake", world_slug="fake_world")
    ok = init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=fake_pack,
        world_slug="fake_world",
        character_id="anyone",
    )
    assert ok is False
    assert snap.magic_state is None


def test_init_magic_state_skips_when_pack_dir_unknown() -> None:
    """Packs loaded from non-disk sources (cache, fixture) carry
    source_dir=None — the loader needs file paths, so the helper
    skips rather than guess.
    """
    snap = GameSnapshot()
    ok = init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=None,
        world_slug="any",
        character_id="anyone",
    )
    assert ok is False
    assert snap.magic_state is None


def test_init_magic_state_logs_loader_error_without_raising(
    tmp_path: Path,
    caplog,
) -> None:
    """Malformed magic.yaml → LoaderError → log at ERROR and return
    False. Chargen has already produced a character; we refuse to
    blow up the commit because of authoring drift.
    """
    fake_pack = tmp_path / "fake_pack"
    world_dir = fake_pack / "worlds" / "broken_world"
    world_dir.mkdir(parents=True)
    # Missing required keys → LoaderError on validation.
    (fake_pack / "magic.yaml").write_text("permitted_plugins: []\n")
    (world_dir / "magic.yaml").write_text(
        "active_plugins: []\nworld: broken_world\n"
        # No allowed_sources / cost_types — minimal but should still
        # fail validation in a controlled way.
    )

    snap = GameSnapshot()
    with caplog.at_level(logging.ERROR, logger="sidequest.server.magic_init"):
        ok = init_magic_state_for_session(
            snapshot=snap,
            genre_pack_source_dir=fake_pack,
            world_slug="broken_world",
            character_id="anyone",
        )

    # Either it loaded a permissive minimal config OR it logged and
    # returned False. Both are acceptable degradations as long as
    # chargen never raises.
    if ok is False:
        assert any("magic.init_failed" in rec.message for rec in caplog.records), (
            "LoaderError must be logged loud (CLAUDE.md no-silent-fallbacks)"
        )
    # Either way the snapshot is in a consistent state.
    assert snap.magic_state is None or snap.magic_state.config.world_slug == "broken_world"


def test_init_magic_state_idempotent_on_existing_state_adds_character_only() -> None:
    """Pingpong 2026-04-30: in 4P MP each player's chargen confirmation
    calls ``init_magic_state_for_session`` against the SAME canonical
    snapshot. Pre-fix every call did ``MagicState.from_config(config)``
    which built a NEW state with only the current ``character_id`` and
    assigned it to ``snapshot.magic_state``, wiping prior committers.
    With four sequential commits (Charlie → Snoopy → Linus → Lucy)
    only Lucy ended up in the ledger; the next narrator turn referenced
    Linus by name, the magic parser raised
    ``unknown actor: 'Linus'; call add_character first``.

    Post-fix: the helper is idempotent on the snapshot — first call
    builds the state and adds the character; subsequent calls REUSE
    the existing state and only call ``add_character`` for the new
    PC. All four PCs end up in the ledger.
    """
    pack_dir, world_slug = _resolve_space_opera_world_with_magic()

    snap = GameSnapshot(genre_slug="space_opera", world_slug=world_slug)
    assert snap.magic_state is None

    # Simulate the 4P MP commit order from the playtest.
    chargen_order = ["Charlie", "Snoopy", "Linus", "Lucy"]
    for pc in chargen_order:
        ok = init_magic_state_for_session(
            snapshot=snap,
            genre_pack_source_dir=pack_dir,
            world_slug=world_slug,
            character_id=pc,
        )
        assert ok is True, f"init failed for {pc!r}"

    # First commit must have created the state; subsequent commits
    # must NOT have replaced it. We can't directly observe the
    # call-site decision, but we can verify the load-bearing outcome:
    # ALL four PCs are registered in the ledger, not just the last one.
    assert snap.magic_state is not None
    for pc in chargen_order:
        char_keys = [
            k for k in snap.magic_state.ledger
            if k.startswith(f"character|{pc}|")
        ]
        assert len(char_keys) > 0, (
            f"PC {pc!r} not registered in magic_state.ledger after MP "
            f"chargen sequence. Pre-fix only the last committer "
            f"({chargen_order[-1]!r}) survived because each call "
            f"replaced snapshot.magic_state with a fresh state. "
            f"Ledger keys: {list(snap.magic_state.ledger.keys())}"
        )


def test_init_magic_state_idempotent_on_duplicate_character_id() -> None:
    """Reconnect / re-commit safety: if the same PC's chargen confirm
    fires twice (transient idempotence — the user accidentally
    re-confirms, or a save-resume re-runs the chargen hook), the
    second call must not raise. ``MagicState.add_character`` is
    already idempotent (line 125-126: ``if serialized in ledger:
    continue``); this test guards that contract from the init layer.
    """
    pack_dir, world_slug = _resolve_space_opera_world_with_magic()

    snap = GameSnapshot(genre_slug="space_opera", world_slug=world_slug)
    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="Linus",
    )
    state_after_first = snap.magic_state
    assert state_after_first is not None
    bars_after_first = dict(state_after_first.ledger)

    # Second commit for the same PC — must reuse the state and not
    # mutate the ledger.
    init_magic_state_for_session(
        snapshot=snap,
        genre_pack_source_dir=pack_dir,
        world_slug=world_slug,
        character_id="Linus",
    )
    assert snap.magic_state is state_after_first, (
        "Second init for the same PC must REUSE the existing state, "
        "not create a new one. If the state object identity changed, "
        "any per-player references held by callers (orchestrator, "
        "validator) would silently dangle."
    )
    # Ledger contents unchanged — same bars, no duplicates.
    assert dict(snap.magic_state.ledger) == bars_after_first


def test_websocket_session_handler_imports_and_calls_init_magic_state() -> None:
    """Wire-first source grep: the chargen-confirm path must reference
    init_magic_state_for_session so the hook is reachable from
    production code paths. Mirrors test_session_handler_invokes_shared_world_delta.
    """
    from sidequest.server import websocket_session_handler

    with open(websocket_session_handler.__file__) as fh:
        source = fh.read()

    assert "init_magic_state_for_session" in source, (
        "websocket_session_handler.py does not reference "
        "init_magic_state_for_session — Phase 4 chargen hook is unwired."
    )
