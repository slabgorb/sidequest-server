"""Story 45-5 — End-to-end wire test for stale-slot reinit clearing.

The unit tests in ``tests/game/test_init_session_clears_stale_slot.py``
spec ``SqliteStore.init_session()`` directly. **This file proves the
seam is wired**: that a real ``ConnectHandler`` driving the legacy
genre/world path actually invokes the cleared reinit on a populated
``.db`` on disk.

Without this wire test, a unit test on a ``clear_session_tables()``
helper alone fails the wire-first bar (CLAUDE.md / Epic 45 wire-first
discipline). It must be possible to land a clear that does not get
called from production — that is the exact "tests pass but nothing is
wired" failure the principle exists to prevent.

**What this exercises:**

1. Pre-populate a save slot at ``save_dir/<genre>/<world>/<player>/save.db``
   with a stale ``narrative_log`` row dated 2026-04-18 (Playtest 3
   evidence shape: prior-day narration carried into a fresh session).
2. Drive ``ConnectHandler`` via ``handler.handle_message`` with a
   ``connect`` event. ``store.load()`` returns ``None`` (no
   ``game_state`` row) so the new-session branch in
   ``handlers/connect.py:906`` runs ``store.init_session()``.
3. Reopen the SQLite file and assert the stale row is gone.
4. Assert the ``session.slot_reinitialized`` watcher event fired.

The legacy genre/world path is the focused seam — both call sites
(slug at ``connect.py:405`` and legacy at ``:906``) drive into the same
``SqliteStore.init_session()``. Per TEA deviation log, exercising the
legacy seam is sufficient because the unit tests cover the persistence
layer; the wire test only needs to prove **one** caller actually
reaches the clear.

**ACs covered:**
- AC1 (wire confirmation): production connect path clears stale rows
- AC2/AC3 (post-chargen turn_manager): after chargen confirmation on
  a previously-populated slot, ``turn_manager.round == 1``, not the
  Playtest 3 wedge value (round=0).
- AC4 (wire confirmation): the watcher event fires when init_session
  runs through the production handler path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from sidequest.game.persistence import SqliteStore, db_path_for_session
from sidequest.game.session import NarrativeEntry
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler
from tests.server.conftest import (
    mock_claude_client_factory as _mock_claude_client_factory,
)

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def handler_factory(save_dir: Path) -> Callable[[], WebSocketSessionHandler]:
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("content pack not found")

    def make() -> WebSocketSessionHandler:
        return WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=save_dir,
        )

    return make


def _seed_stale_save_slot(
    save_dir: Path,
    *,
    genre: str = "caverns_and_claudes",
    world: str = "grimvault",
    player: str = "Persistent",
) -> Path:
    """Create a ``.db`` at the canonical save-path with a single stale
    ``narrative_log`` row but **no** ``game_state`` row.

    No ``game_state`` is critical: it makes ``store.load()`` return
    ``None``, which is what routes the connect handler into the
    ``init_session()`` branch (production seam under test). With a
    ``game_state`` row, the resume path runs and the bug doesn't
    surface.
    """
    db = db_path_for_session(save_dir, genre, world, player)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore.open(str(db))
    # First init seeds session_meta from a prior session. The genre/world
    # match the connecting client so we can isolate the clear behavior
    # from any genre-mismatch logic.
    store.init_session(genre, world)
    # Stale narrative_log row from "yesterday" — Playtest 3 prot_thokk
    # had narrative_log dated 2026-04-18 in a save created 2026-04-19.
    store.append_narrative(
        NarrativeEntry(
            timestamp=0,
            round=5,
            author="narrator",
            content="Stale prior-day narration that must not survive reinit.",
            tags=["prior-day"],
        )
    )
    store.close()
    return db


async def _connect(
    handler: WebSocketSessionHandler,
    *,
    player_name: str = "Persistent",
    world: str = "grimvault",
) -> list[object]:
    payload = SessionEventPayload(
        event="connect",
        player_name=player_name,
        genre="caverns_and_claudes",
        world=world,
    )
    out = await handler.handle_message(SessionEventMessage(payload=payload, player_id=""))
    assert isinstance(out[0], SessionEventMessage), (
        f"connect must return a SessionEventMessage as the first frame; "
        f"got {type(out[0]).__name__ if out else 'empty list'}"
    )
    assert not isinstance(out[0], ErrorMessage), (
        f"connect returned an ErrorMessage: {getattr(out[0].payload, 'message', out[0])!r}"
    )
    return out


async def _walk_and_confirm_chargen(handler: WebSocketSessionHandler) -> list:
    """Walk a deterministic chargen path against ``caverns_and_claudes``
    and submit the confirmation message. Mirrors the helper in
    ``tests/server/test_chargen_persist_and_play.py`` so the wire flow
    is consistent with neighbour stories."""
    sd = handler._session_data  # type: ignore[attr-defined]
    builder = sd.builder
    assert builder is not None, (
        "post-connect handler must have a CharacterBuilder when no "
        "character is persisted — this is the precondition the reinit "
        "path establishes"
    )

    while not builder.is_confirmation():
        scene = builder.current_scene()
        if scene.choices:
            payload = CharacterCreationPayload(phase="scene", choice="1")
        elif scene.allows_freeform:
            payload = CharacterCreationPayload(phase="scene", choice="Rux")
        else:
            payload = CharacterCreationPayload(phase="continue")
        out = await handler.handle_message(
            CharacterCreationMessage(payload=payload, player_id="pid")
        )
        if out and isinstance(out[0], ErrorMessage):
            raise AssertionError(f"chargen walk error: {out[0].payload.message}")

    return await handler.handle_message(
        CharacterCreationMessage(
            payload=CharacterCreationPayload(phase="confirmation"),
            player_id="pid",
        )
    )


# ---------------------------------------------------------------------------
# AC1 (wire) — production connect path actually clears the stale slot.
# ---------------------------------------------------------------------------


def test_connect_on_populated_slot_clears_narrative_log(
    handler_factory, save_dir: Path
) -> None:
    """The Playtest 3 reproduction at the wire seam: a save with a stale
    ``narrative_log`` row connects via the legacy genre/world path. The
    connect must drive ``init_session()`` and the stale row must be gone
    from disk after the handler returns."""

    db = _seed_stale_save_slot(save_dir)
    # Sanity: stale row really is there before connect.
    pre = SqliteStore.open(str(db))
    pre_count = pre._conn.execute(
        "SELECT COUNT(*) FROM narrative_log"
    ).fetchone()[0]
    pre.close()
    assert pre_count == 1, (
        f"fixture sanity: stale narrative_log row not seeded (count={pre_count})"
    )

    async def body() -> None:
        handler = handler_factory()
        await _connect(handler)
        await handler.cleanup()

    asyncio.run(body())

    # Re-open the file from disk — fresh connection, no in-memory bias.
    post = SqliteStore.open(str(db))
    post_count = post._conn.execute(
        "SELECT COUNT(*) FROM narrative_log"
    ).fetchone()[0]
    post.close()

    assert post_count == 0, (
        "AC1 wire violation: after a connect that drives the new-session "
        "branch, the stale narrative_log row from a prior session "
        "survived. The Playtest 3 prot_thokk/hant bug is not fixed at "
        "the production seam — even if SqliteStore.init_session() "
        "clears in unit tests, ConnectHandler is not reaching it."
    )


# ---------------------------------------------------------------------------
# AC4 (wire) — watcher event fires through the production handler path.
# ---------------------------------------------------------------------------


def test_connect_emits_session_slot_reinitialized_watcher_event(
    handler_factory, save_dir: Path
) -> None:
    """The watcher event must fire through the real connect handler so
    Sebastien's GM panel renders the reinit row when the actual
    production code runs (not just when a unit test calls init_session
    directly). Wire-first — unit-only OTEL coverage is not enough
    (CLAUDE.md OTEL principle)."""

    _seed_stale_save_slot(save_dir)

    async def body() -> list:
        handler = handler_factory()
        # Patch at the persistence module — that is where init_session()
        # invokes the publish. If Dev decides to publish from the handler
        # instead (less ideal — the persistence layer owns the state
        # mutation), the patch target moves and this test fails loudly,
        # which is a useful signal.
        with patch(
            "sidequest.game.persistence._watcher_publish",
        ) as wp:
            await _connect(handler)
            await handler.cleanup()
        return list(wp.call_args_list)

    calls = asyncio.run(body())

    event_calls = [
        call for call in calls
        if call.args and call.args[0] == "session.slot_reinitialized"
    ]
    assert len(event_calls) >= 1, (
        "AC4 wire violation: a real connect against a populated slot "
        "must emit at least one session.slot_reinitialized watcher "
        "event. Sebastien's GM panel cannot render the reinit row "
        f"without this. Saw publishes: "
        f"{[(c.args[0] if c.args else None) for c in calls]!r}"
    )


# ---------------------------------------------------------------------------
# AC2 / AC3 — turn_manager is not frozen at the Playtest 3 wedge values
# after chargen confirmation on a previously-populated slot.
# ---------------------------------------------------------------------------


def test_post_chargen_turn_manager_is_fresh_after_stale_slot_reinit(
    handler_factory, save_dir: Path
) -> None:
    """Playtest 3 (2026-04-19): prot_thokk's turn_manager was wedged at
    ``round=0, interaction=2`` after the new-session branch fired
    against a slot whose narrative_log was not cleared. The narrator's
    prompt saw the stale entry and the counter disagreed with the log's
    ``max(round_number)``; turn 1 never fired.

    With clear-on-reinit, post-chargen the turn_manager must hold the
    fresh ``materialize_from_genre_pack`` defaults — neither wedged nor
    inheriting any prior state."""

    db = _seed_stale_save_slot(save_dir)

    async def body():
        handler = handler_factory()
        await _connect(handler)
        await _walk_and_confirm_chargen(handler)
        sd = handler._session_data  # type: ignore[attr-defined]
        return sd.snapshot.turn_manager.round, sd.snapshot.turn_manager.interaction

    round_, interaction = asyncio.run(body())

    # Post-chargen, ``materialize_from_genre_pack`` → ``replace_with()``
    # writes fresh ``TurnManager(round=1, interaction=1)``. Chargen
    # confirmation then fires the first narration turn, which bumps
    # ``interaction`` (typically 1→2) but leaves ``round`` at 1. The
    # Playtest 3 wedge values were round=0/interaction=2 — round=0 is
    # impossible from the fresh default, so a non-1 ``round`` here means
    # stale state survived the reinit.
    assert round_ == 1, (
        f"AC2/AC3 violation: post-chargen turn_manager.round={round_}, "
        f"interaction={interaction}; must be exactly round=1 (the "
        "materialize_from_genre_pack default). round=0 is the Playtest 3 "
        "prot_thokk/hant freeze; round>1 means stale state survived the "
        "reinit."
    )

    # AC1+AC3 round-trip: the stale prior-day row that we seeded must
    # not be the active context the narrator's first turn ran against.
    # If it survives the reinit, it appears in `narrative_log` BEFORE
    # any chargen-era entries, polluting `recent_narrative()` (the
    # narrator's prompt input). Post-fix, the only rows present are
    # chargen-era and forward.
    post = SqliteStore.open(str(db))
    rows = post._conn.execute(
        "SELECT round_number, content FROM narrative_log ORDER BY id ASC"
    ).fetchall()
    post.close()
    stale_rows = [
        (r[0], r[1])
        for r in rows
        if r[1] and "Stale prior-day narration" in r[1]
    ]
    assert not stale_rows, (
        "AC3 violation: the stale prior-day narrative_log row is still "
        "present in the recent narrative log after a full chargen walk. "
        "The narrator's first-turn prompt was given polluted context — "
        "exactly the Playtest 3 root cause. Stale rows present: "
        f"{stale_rows!r}"
    )


def test_post_connect_narrative_log_is_clean_for_fresh_chargen(
    handler_factory, save_dir: Path
) -> None:
    """Tighter AC1 wire variant — at the moment chargen begins (post-
    connect, pre-confirmation), the narrative_log on disk must already
    be clean. Otherwise the narrator's first prompt sees yesterday's
    text and re-bootstraps from polluted context (the Playtest 3 root
    cause)."""

    db = _seed_stale_save_slot(save_dir)

    async def body() -> None:
        handler = handler_factory()
        await _connect(handler)
        await handler.cleanup()

    asyncio.run(body())

    post = SqliteStore.open(str(db))
    rows = post._conn.execute(
        "SELECT round_number, content FROM narrative_log"
    ).fetchall()
    post.close()
    stale = [
        r for r in rows
        if r[1] and "Stale prior-day narration" in r[1]
    ]
    assert not stale, (
        "AC1 wire violation: the stale 'Stale prior-day narration' row "
        "is still present in narrative_log after connect. Specifically "
        f"got rows: {[(r[0], r[1][:50]) for r in rows]!r}. The "
        "narrator's prompt will see yesterday's text on turn 1."
    )


# ---------------------------------------------------------------------------
# Wiring proof — the import surface is intact end-to-end. If the
# refactor that extracted ConnectHandler ever moves init_session away
# from the new-session branch, this grep test catches it before the
# wire test fails the more subtle "rows not cleared" assertion.
# ---------------------------------------------------------------------------


def test_connect_handler_invokes_init_session_on_legacy_path() -> None:
    """Source-grep wire-test: ``ConnectHandler.handle()`` must contain a
    call to ``store.init_session(`` in the legacy genre/world branch.
    This pins the import surface so a refactor cannot silently bypass
    the seam — the failure mode would be a wire test that passes
    because init_session is never called (so no stale rows to clear,
    but no clear either)."""

    connect_path = (
        Path(__file__).resolve().parents[2]
        / "sidequest"
        / "handlers"
        / "connect.py"
    )
    src = connect_path.read_text(encoding="utf-8")
    assert "store.init_session(" in src, (
        "ConnectHandler must invoke store.init_session() — Story 45-5 "
        "depends on this seam being live in the legacy and slug "
        "branches. If the call was removed during a refactor, the wire "
        "test below will pass vacuously (no stale rows → nothing to "
        "clear → false green)."
    )
