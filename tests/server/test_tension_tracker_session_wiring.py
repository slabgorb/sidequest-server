"""Story 42-4 AC6 — TensionTracker ownership on _SessionData.

AC6 requires:
  1. ``_SessionData`` owns a ``TensionTracker`` instance, constructed at
     session bind (not lazily inside dispatch).
  2. Each PLAYER_ACTION dispatch calls ``tracker.tick(...)`` with the
     per-turn inputs (action classification + stakes).
  3. The resulting ``PacingHint`` is serialised into
     ``TurnContext.pacing_hint`` and surfaces in the narrator's Early zone.
  4. A Phase-1/2 narrative-only scene (no encounter) still keeps a
     tracker — narrative scenes also have pacing — but the section may be
     omitted if the tracker hasn't accumulated enough signal.
  5. ``pacing_hint is None`` suppresses the narrator-prompt section
     entirely (no empty-string stubs).

42-3 ported ``TensionTracker`` and ``PacingHint``. 42-4 wires them into
the session lifecycle. These tests are RED on introduction because no
existing code path holds or ticks a tracker — all ``TurnContext.pacing_hint``
sites in the codebase default the field to ``None`` and never set it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS, GenreLoader

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bound_session_data():
    """Return a freshly-bound ``_SessionData`` for caverns_and_claudes.

    Mirrors the construction site in session_handler._handle_connect — TEA
    does not reach into the handler, to keep this a pure wiring test.
    """
    from sidequest.server.session_handler import _SessionData

    pack = GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load("caverns_and_claudes")
    snap = GameSnapshot(genre_slug="caverns_and_claudes")
    return _SessionData(
        genre_slug="caverns_and_claudes",
        world_slug="crypt_of_the_seven",
        player_name="Rux",
        player_id="p1",
        snapshot=snap,
        store=MagicMock(),
        genre_pack=pack,
        orchestrator=MagicMock(),
    )


# ---------------------------------------------------------------------------
# AC6.1 — _SessionData owns a TensionTracker
# ---------------------------------------------------------------------------


def test_session_data_declares_tension_tracker_field(bound_session_data) -> None:
    """_SessionData must declare ``tension_tracker`` so the handler can tick it.

    Without the field, no dispatch-site can obtain the session-scoped tracker
    and the Early-zone pacing_hint section is permanently dead.
    """
    assert hasattr(bound_session_data, "tension_tracker"), (
        "_SessionData is missing a ``tension_tracker`` attribute. Add one to "
        "the dataclass with a default_factory so every session gets a fresh "
        "tracker at construction."
    )


def test_tension_tracker_is_instantiated_at_session_bind(bound_session_data) -> None:
    """The tracker must be real, not a None placeholder.

    Lazy construction inside dispatch risks multiple tracker instances per
    turn; session-scoped ownership is the Rust parity.
    """
    from sidequest.game.tension_tracker import TensionTracker

    tracker = getattr(bound_session_data, "tension_tracker", None)
    assert isinstance(tracker, TensionTracker), (
        f"_SessionData.tension_tracker must be a TensionTracker instance at "
        f"session construction time; got {type(tracker).__name__}."
    )


def test_distinct_sessions_get_distinct_trackers() -> None:
    """Each _SessionData instance must hold its own tracker.

    Shared trackers would leak tension across sessions — an obvious
    mutable-default-argument trap when porting from Rust's
    ``Arc<Mutex<TensionTracker>>`` to Python.
    """
    from sidequest.server.session_handler import _SessionData

    pack = GenreLoader(DEFAULT_GENRE_PACK_SEARCH_PATHS).load("caverns_and_claudes")

    def _make() -> _SessionData:
        return _SessionData(
            genre_slug="caverns_and_claudes",
            world_slug="crypt_of_the_seven",
            player_name="Rux",
            player_id="p1",
            snapshot=GameSnapshot(genre_slug="caverns_and_claudes"),
            store=MagicMock(),
            genre_pack=pack,
            orchestrator=MagicMock(),
        )

    sd_a = _make()
    sd_b = _make()
    assert sd_a.tension_tracker is not sd_b.tension_tracker, (
        "Two sessions are sharing a TensionTracker — mutable default or "
        "class-level attribute. Use ``field(default_factory=TensionTracker)``."
    )


# ---------------------------------------------------------------------------
# AC6.2 / AC6.3 — tracker ticks per turn; PacingHint reaches TurnContext
# ---------------------------------------------------------------------------


def test_build_turn_context_populates_pacing_hint_when_tracker_has_state(
    bound_session_data,
) -> None:
    """After the tracker has accumulated state, _build_turn_context must
    surface a ``PacingHint`` on the returned ``TurnContext``.

    Per AC6: "Produced PacingHint serialises into TurnContext.pacing_hint."
    """
    from sidequest.game.tension_tracker import TensionTracker
    from sidequest.server.session_handler import _build_turn_context

    # Prime the tracker with a value that guarantees hint emission — use
    # ``with_values`` which is the seam 42-3 landed for deterministic tests.
    bound_session_data.tension_tracker = TensionTracker.with_values(
        action=0.9, stakes=0.9
    )

    ctx = _build_turn_context(bound_session_data)
    assert ctx.pacing_hint is not None, (
        "TurnContext.pacing_hint was None after seeding a high-tension "
        "TensionTracker. _build_turn_context is not consulting "
        "sd.tension_tracker."
    )


def test_build_turn_context_omits_pacing_hint_when_tracker_is_quiet(
    bound_session_data,
) -> None:
    """A fresh / quiet tracker yields a None hint — no narrator section.

    Per AC6: "pacing_hint is None → no section" — suppress, do not
    emit an empty-string placeholder.
    """
    from sidequest.server.session_handler import _build_turn_context

    # A freshly-constructed tracker (action=stakes=0) produces no hint.
    ctx = _build_turn_context(bound_session_data)
    assert ctx.pacing_hint is None, (
        "TurnContext.pacing_hint must be None when the tracker has no "
        "accumulated signal; got "
        f"{ctx.pacing_hint!r}. Emitting an empty-string hint injects a stub "
        "section into the narrator prompt — ADR-009 attention-aware zones "
        "must be skipped cleanly when empty."
    )


def test_dispatch_path_ticks_tracker_once_per_turn(bound_session_data) -> None:
    """PLAYER_ACTION dispatch must call ``tension_tracker.tick(...)`` exactly once.

    Multiple ticks per turn would double-count action/stakes inputs and
    escalate PacingHint incorrectly. Zero ticks would flatline the tracker
    and kill the Early-zone section permanently.
    """
    from sidequest.game.tension_tracker import TensionTracker

    tick_count = {"n": 0}

    class _CountingTracker(TensionTracker):
        def tick(self, *args, **kwargs):  # type: ignore[override]
            tick_count["n"] += 1
            return super().tick(*args, **kwargs)

    bound_session_data.tension_tracker = _CountingTracker()

    # Exercise the per-turn tick seam. The wiring is the responsibility of
    # Dev during GREEN; TEA simply declares that the seam must exist.
    #
    # If ``tick_per_turn`` does not exist yet, this ImportError is the
    # RED signal — Dev adds the helper during GREEN.
    try:
        from sidequest.server.session_handler import tick_tension_tracker_for_turn
    except ImportError as exc:  # noqa: BLE001 - intentional: diagnostic for RED
        pytest.fail(
            "session_handler.tick_tension_tracker_for_turn is not yet "
            "exported. AC6 wiring: add a helper that dispatch-time code "
            "calls exactly once per PLAYER_ACTION to advance the session's "
            "TensionTracker.\n\nImportError was: " + str(exc)
        )

    tick_tension_tracker_for_turn(bound_session_data, action="explore", stakes="low")
    assert tick_count["n"] == 1, (
        f"Expected tracker.tick() to fire exactly once per turn; saw "
        f"{tick_count['n']}."
    )


# ---------------------------------------------------------------------------
# AC6.5 — narrator Early-zone section skipped when pacing_hint is None
# ---------------------------------------------------------------------------


def test_narrator_prompt_omits_early_zone_when_pacing_hint_is_none(
    bound_session_data,
) -> None:
    """Orchestrator must not emit an Early-zone section when pacing_hint is None.

    No empty-string sections in attention-aware prompts (ADR-009).
    """
    from sidequest.agents.orchestrator import TurnContext

    ctx = TurnContext(character_name="Rux", in_combat=False, pacing_hint=None)

    # Re-use the prompt zone registration surface Orchestrator offers for
    # Phase 2 tests. If the surface does not expose a way to introspect
    # the Early zone, the test fails with a clear RED signal.
    #
    # AC6 wiring contract: ``orchestrator.build_prompt_zones(ctx)`` returns
    # a dict keyed by zone name (``early``, ``valley``, ``late``) whose
    # ``early`` value is a dict of section_name → section_text.
    try:
        from sidequest.agents.orchestrator import (  # type: ignore[attr-defined]
            build_prompt_zones,
        )
    except ImportError:
        pytest.fail(
            "orchestrator.build_prompt_zones(ctx) is not exported. AC6 "
            "wiring: expose a deterministic zone-map builder so the "
            "Early-zone pacing section can be asserted absent/present "
            "without string-searching full prompts."
        )

    zones = build_prompt_zones(ctx)
    early_sections = zones.get("early", {}) or {}
    assert "pacing_hint" not in early_sections, (
        "Early-zone ``pacing_hint`` section appeared even though "
        "TurnContext.pacing_hint was None. Suppress the section entirely "
        "instead of emitting an empty-string stub."
    )
