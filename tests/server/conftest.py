"""Shared pytest fixtures for sidequest-server server-layer tests.

Centralizes the Claude-client mock used by every dispatch test. Before
Story 2.3 Slice H the mock could be a bare ``AsyncMock()`` because
``_chargen_confirmation`` never invoked the orchestrator — the
narration path only fired on PLAYER_ACTION. Slice H routes an opening
turn through the orchestrator at confirmation, so every chargen test
now goes through the narrator pipeline and the mock has to return a
real :class:`ClaudeResponse` with non-empty text + session id.

Also installs a daemon guard: every test in this directory runs with
``DaemonClient`` replaced by an always-unavailable stub so that
narration-turn post-processing (lore embedding, image render) never
blocks on a real Unix-domain-socket round trip. Integration tests that
want to exercise the daemon path use an in-process fake
(``test_render_dispatch.py``'s asyncio Unix server,
``test_lore_rag_wiring.py``'s counting stub) — their
``monkeypatch.setattr`` call simply overrides the guard for that test.
No server test ever talks to the real ``/tmp/sidequest-renderer.sock``.

Also installs a genre-pack search-path guard: every test in this directory
resolves genre packs from ``tests/fixtures/packs/`` (the frozen fixture
pack at ``test_genre/`` with symlinks for each real genre slug) rather than
from ``sidequest-content/``. This makes the suite hermetic — no CI
dependency on the content submodule. Tests that construct
``WebSocketSessionHandler`` with an explicit ``genre_pack_search_paths``
argument (e.g. ``test_session_handler_slug_resumed.py``) bypass this
guard intentionally and must handle their own content-not-found skips.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sidequest.agents.claude_client import ClaudeResponse

# Absolute path to the frozen fixture pack directory.
# Structure: tests/fixtures/packs/{test_genre,caverns_and_claudes,...} where
# every slug is a symlink → test_genre (mutant_wasteland frozen copy).
_FIXTURE_PACKS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "packs"


# ---------------------------------------------------------------------------
# Daemon guard — autouse. Prevents any server test from reaching the real
# /tmp/sidequest-renderer.sock, which would otherwise burn up to 180 s per
# embed()/render() call when the daemon is slow, warming, or dead.
# ---------------------------------------------------------------------------


class _UnavailableDaemonClient:
    """Stand-in for ``DaemonClient`` that never admits to being available.

    ``is_available()`` returns ``False``, matching the natural fail-fast
    branch already handled by ``session_handler._maybe_dispatch_render``
    and ``lore_embedding.{retrieve_lore_context,embed_pending_fragments}``.
    Any accidental call into ``embed()`` / ``render()`` raises loudly
    instead of hanging — that's the whole point of the guard.
    """

    socket_path = Path("/tmp/sq-test-daemon-not-used.sock")

    def is_available(self) -> bool:
        return False

    async def embed(self, text: str):  # noqa: ARG002
        raise RuntimeError(
            "DaemonClient.embed called in a server test without opting in "
            "(mark @pytest.mark.live_daemon or patch the symbol yourself)."
        )

    async def render(self, params):  # noqa: ARG002
        raise RuntimeError(
            "DaemonClient.render called in a server test without opting in "
            "(mark @pytest.mark.live_daemon or patch the symbol yourself)."
        )


@pytest.fixture(autouse=True)
def _mock_daemon_client(monkeypatch):
    """Autouse guard: replace ``DaemonClient`` with an always-unavailable
    stub everywhere the server code instantiates one inline.

    No test talks to the real daemon. Integration tests that want a
    daemon fake (``test_render_dispatch.py``, ``test_lore_rag_wiring.py``)
    install their own via ``monkeypatch.setattr`` — those patches shadow
    this one for the duration of that test and teardown unwinds in LIFO.
    """
    monkeypatch.setattr(
        "sidequest.server.session_handler.DaemonClient",
        lambda *a, **kw: _UnavailableDaemonClient(),
    )
    monkeypatch.setattr(
        "sidequest.game.lore_embedding.DaemonClient",
        lambda *a, **kw: _UnavailableDaemonClient(),
    )


# ---------------------------------------------------------------------------
# Fixture pack search-path guard — autouse. Redirects all genre pack
# resolution to tests/fixtures/packs/ so the test suite never depends on
# sidequest-content being present on disk.
#
# Every genre slug used in tests (caverns_and_claudes, elemental_harmony,
# mutant_wasteland, spaghetti_western, space_opera, heavy_metal, low_fantasy,
# neon_dystopia) resolves via a symlink in that directory that points to the
# frozen test_genre/ pack (a stripped copy of mutant_wasteland).
#
# Tests that pass genre_pack_search_paths explicitly to
# WebSocketSessionHandler (e.g. test_session_handler_slug_resumed.py) are
# NOT affected — they construct their own loader with a fixed path and add
# their own pytest.skip guards for when sidequest-content is missing.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fixture_pack_search_paths(monkeypatch):
    """Autouse guard: point DEFAULT_GENRE_PACK_SEARCH_PATHS at the frozen
    fixture pack directory so genre resolution never reaches sidequest-content.
    """
    monkeypatch.setattr(
        "sidequest.genre.loader.DEFAULT_GENRE_PACK_SEARCH_PATHS",
        [_FIXTURE_PACKS_DIR],
    )


def canned_claude_response(
    *,
    text: str | None = None,
    session_id: str = "test-session",
) -> ClaudeResponse:
    """Build a minimally-valid :class:`ClaudeResponse` for narration tests.

    The orchestrator's ``game_patch`` extraction regex runs on
    ``text``; an empty / missing fence block is fine — extraction
    falls back to ``{}`` and the narration pipeline completes. Tests
    that care about state deltas override ``text`` to include a
    ```game_patch``` fence.
    """
    return ClaudeResponse(
        text=text
        or (
            "The world takes shape around you. Light filters through the "
            "morning haze and the day begins.\n\n"
            "```game_patch\n{}\n```"
        ),
        session_id=session_id,
        input_tokens=100,
        output_tokens=60,
    )


def make_mock_claude_client(
    *,
    text: str | None = None,
    session_id: str = "test-session",
) -> MagicMock:
    """Return a Claude client mock with ``send_with_session`` wired to
    yield a canned :class:`ClaudeResponse`.

    Tests that want to inspect the prompt sent to Claude can access
    ``mock.send_with_session`` (an :class:`AsyncMock`) and its
    ``call_args`` after invocation.
    """
    mock = MagicMock()
    mock.send_with_session = AsyncMock(
        return_value=canned_claude_response(text=text, session_id=session_id)
    )
    return mock


def mock_claude_client_factory(
    *,
    text: str | None = None,
    session_id: str = "test-session",
):
    """Factory suitable for ``WebSocketSessionHandler(claude_client_factory=...)``."""
    client = make_mock_claude_client(text=text, session_id=session_id)
    return lambda: client


@pytest.fixture
def session_handler_factory(tmp_path):
    """Return a factory callable ``(genre: str) -> (sd, handler)``.

    Builds a minimal ``_SessionData`` + ``WebSocketSessionHandler`` suitable
    for unit-testing ``_execute_narration_turn`` without a real WebSocket or
    LLM call. The test is responsible for overriding
    ``sd.orchestrator.run_narration_turn`` with an ``AsyncMock``.

    Task 11 (story 3.4): used by test_confrontation_dispatch_wiring.py.
    Task 16 (story 3.4): snapshot now includes a Character named "Rux" so
    XP-award tests can inspect ``sd.snapshot.characters[0].core.xp``.
    """

    from sidequest.agents.orchestrator import Orchestrator
    from sidequest.game.character import Character
    from sidequest.game.creature_core import CreatureCore, Inventory
    from sidequest.game.persistence import SqliteStore
    from sidequest.game.session import GameSnapshot
    import sidequest.genre.loader as _genre_loader_mod
    from sidequest.genre.loader import GenreLoader
    from sidequest.server.session_handler import (
        WebSocketSessionHandler,
        _SessionData,
    )

    def _make(genre: str):
        # Read DEFAULT_GENRE_PACK_SEARCH_PATHS from the module at call-time so
        # that the _fixture_pack_search_paths monkeypatch is visible here.
        pack = GenreLoader(_genre_loader_mod.DEFAULT_GENRE_PACK_SEARCH_PATHS).load(genre)
        snap = GameSnapshot(genre_slug=genre)
        core = CreatureCore(
            name="Rux",
            description="A stoic fighter",
            personality="stoic",
            inventory=Inventory(),
        )
        char = Character(
            core=core,
            char_class="Fighter",
            race="Human",
            backstory="A wandering fighter",
        )
        snap.characters.append(char)
        store = SqliteStore.open_in_memory()
        orch = MagicMock(spec=Orchestrator)
        sd = _SessionData(
            genre_slug=genre,
            world_slug="",
            player_name="Rux",
            player_id="player-1",
            snapshot=snap,
            store=store,
            genre_pack=pack,
            orchestrator=orch,
        )
        handler = WebSocketSessionHandler(save_dir=tmp_path)
        handler._session_data = sd
        return sd, handler

    return _make


# ---------------------------------------------------------------------------
# Group B Task 10 — session_fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def session_fixture():
    """Return ``(sd, handler)`` — a minimal in-memory _SessionData + its handler.

    ``sd.local_dm`` is populated by the default_factory added in Task 10.
    ``sd.orchestrator`` is a ``MagicMock`` — tests that exercise the narrator
    path override ``run_narration_turn`` via ``patch.object``.

    The handler is a :class:`WebSocketSessionHandler` wired to a stub
    save directory; its ``_session_data`` attribute is set to ``sd`` so
    ``_execute_narration_turn`` can be called directly without going through
    the full connect handshake.
    """
    from sidequest.game.session import GameSnapshot
    from sidequest.game.turn import TurnManager
    from sidequest.server.session_handler import (
        WebSocketSessionHandler,
        _SessionData,
    )

    snap = GameSnapshot(
        genre_slug="caverns_and_claudes",
        world_slug="sunken_keep",
        location="Main Hall",
        turn_manager=TurnManager(interaction=1),
    )
    sd = _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="sunken_keep",
        player_name="TestHero",
        player_id="player:TestHero",
        snapshot=snap,
        store=MagicMock(),
        genre_pack=MagicMock(),
        orchestrator=MagicMock(),
    )
    # Silence the persist side-effect so _execute_narration_turn doesn't fail
    # on sd.store.save / sd.store.append_narrative.
    sd.store.save = MagicMock()
    sd.store.append_narrative = MagicMock()

    handler = WebSocketSessionHandler(save_dir=Path("/tmp/sq-test-never-used"))
    handler._session_data = sd
    return sd, handler


def _build_turn_context_for_test(sd):
    """Build a minimal :class:`TurnContext` from session state.

    Mirrors the shape that ``_build_turn_context`` produces so that
    ``_execute_narration_turn`` receives a plausible context object.
    """
    from sidequest.agents.orchestrator import TurnContext

    return TurnContext(
        state_summary="(test state summary)",
        genre=sd.genre_slug,
        character_name=sd.player_name,
        current_location=getattr(sd.snapshot, "location", None) or "Unknown",
        npc_registry=list(getattr(sd.snapshot, "npc_registry", [])),
    )


def _make_minimal_narration_turn_result(narration: str = "ok"):
    """Construct a :class:`NarrationTurnResult` with minimum required fields."""
    from sidequest.agents.orchestrator import NarrationTurnResult

    return NarrationTurnResult(
        narration=narration,
        is_degraded=False,
        agent_duration_ms=1,
    )
