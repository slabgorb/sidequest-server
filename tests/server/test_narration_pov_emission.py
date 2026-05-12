"""Wiring test — 3-PC MP session per-recipient POV emission (Story 49-8).

End-to-end proof that the new narration projection lands at the wire:

  - Carl's outbound queue receives "You plant a boot..." (2nd-person)
    because he is the anchor of his own action card.
  - Donut's queue receives "Carl plants a boot..." (3rd-person)
    unchanged for that same card.
  - Katia's queue receives "Carl plants a boot..." (3rd-person)
    unchanged for that same card.
  - All three players receive all three cards (no perception filtering
    in this story — that is ADR-028 follow-up).

This test exercises ``emit_event`` via ``WebSocketSessionHandler._emit_event``
just like ``test_perception_rewriter_wiring.py`` — pure dict payloads with
a fake message class so we can read the dict that landed on each queue.

RED until:
  (1) ``sidequest.server.visibility_classifier.classify_narration_visibility``
      exists,
  (2) ``sidequest.agents.pov_swap.swap_to_second_person`` exists,
  (3) the emit pipeline routes NARRATION through both — first to stamp
      the sidecar, then to swap text per-recipient at fan-out.

The sentinel here is the wire-level text on each player's queue. If a
recipient whose ``player_id_to_character[pid] == anchor_pc`` does NOT
see 2nd-person prose, the wiring is broken.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import CreatureCore, Inventory
from sidequest.game.event_log import EventLog
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_slug,
    upsert_game,
)
from sidequest.game.projection.cache import ProjectionCache
from sidequest.game.projection.composed import ComposedFilter
from sidequest.game.projection.rules import load_rules_from_yaml_str
from sidequest.game.session import GameSnapshot
from sidequest.server.session_handler import (
    WebSocketSessionHandler,
    _SessionData,
)
from sidequest.server.session_room import RoomRegistry

_GENRE = "caverns_and_claudes"
_WORLD = "sunden"
_SLUG = "pov-emission-wiring"
_FIXTURE_PACKS = Path(__file__).resolve().parents[1] / "fixtures" / "packs"

_RULES_YAML = """
rules:
  - kind: NARRATION
    visibility_tag: {}
"""


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _pc(name: str, pronouns: str = "he/him") -> Character:
    core = CreatureCore(
        name=name,
        description="A test subject.",
        personality="Test.",
        inventory=Inventory(),
    )
    return Character(
        core=core,
        backstory="A wanderer.",
        char_class="Fighter",
        race="Human",
        pronouns=pronouns,
    )


def _seed_game_row(tmp_path: Path) -> SqliteStore:
    db = db_path_for_slug(tmp_path, _SLUG)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(db)
    store.initialize()
    upsert_game(
        store,
        slug=_SLUG,
        mode=GameMode.MULTIPLAYER,
        genre_slug=_GENRE,
        world_slug=_WORLD,
    )
    return store


def _make_handler_three_pcs(tmp_path: Path) -> WebSocketSessionHandler:
    """Build a handler with three connected PCs (Carl/Donut/Katia)
    seated in a MULTIPLAYER room. Mirrors the 2026-05-12 playtest
    layout."""
    handler = WebSocketSessionHandler(save_dir=tmp_path, genre_pack_search_paths=[_FIXTURE_PACKS])
    snap = GameSnapshot(genre_slug=_GENRE, world_slug=_WORLD)
    snap.characters = [
        _pc("Carl", pronouns="he/him"),
        _pc("Donut", pronouns="he/him"),
        _pc("Katia", pronouns="she/her"),
    ]
    handler._session_data = _SessionData.__new__(_SessionData)
    handler._session_data.snapshot = snap
    handler._session_data.player_id = "p_carl"  # Carl is the emitter
    handler._session_data.genre_slug = _GENRE
    handler._session_data.world_slug = _WORLD

    store = _seed_game_row(tmp_path)
    handler._event_log = EventLog(store)
    handler._projection_filter = ComposedFilter(
        rules=load_rules_from_yaml_str(_RULES_YAML),
        pack_slug=_GENRE,
    )
    handler._projection_cache = ProjectionCache(store)

    registry = RoomRegistry()
    room = registry.get_or_create(slug=_SLUG, mode=GameMode.MULTIPLAYER)
    room.connect("p_carl", socket_id="sock-carl")
    room.connect("p_donut", socket_id="sock-donut")
    room.connect("p_katia", socket_id="sock-katia")
    handler._room = room
    return handler


def _attach_queues(room) -> dict[str, asyncio.Queue]:
    q_carl: asyncio.Queue = asyncio.Queue()
    q_donut: asyncio.Queue = asyncio.Queue()
    q_katia: asyncio.Queue = asyncio.Queue()
    room.attach_outbound("sock-carl", q_carl)
    room.attach_outbound("sock-donut", q_donut)
    room.attach_outbound("sock-katia", q_katia)
    return {"p_carl": q_carl, "p_donut": q_donut, "p_katia": q_katia}


# ---------------------------------------------------------------------------
# 1. Carl receives 2nd-person for his own action card
# ---------------------------------------------------------------------------


def test_anchor_recipient_sees_second_person_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recipient whose player_id maps to the card's anchor_pc must
    see the prose rewritten to 2nd-person ('You plant a boot...').

    This is the load-bearing wiring assertion for Story 49-8. Without
    this, the 2026-05-12 playtest bug is unfixed: every player sees
    every per-PC card third-person.
    """
    handler = _make_handler_three_pcs(tmp_path)
    queues = _attach_queues(handler._room)

    # Fake message class so we can read the dict each player sees.
    from sidequest.server import session_handler as handler_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)

    # Build a NARRATION payload anchored on Carl. Production code
    # builds this via classify_narration_visibility; here we hand-build
    # the dict to keep the test focused on emit-pipeline wiring.
    payload = {
        "text": "Carl plants a boot on the moth's thorax.",
        "footnotes": [],
        "_visibility": {
            "visible_to": "all",
            "fidelity": {},
            "anchor_pc": "Carl",
            "pov_strategy": "pc_anchored",
        },
    }

    # The handler must consult its view to map anchor_pc -> player_id.
    # We seed the view's mapping via SessionGameStateView fixtures
    # consumed by emit_event.
    from sidequest.server import views as views_module

    monkeypatch.setattr(
        views_module,
        "status_effects_by_player",
        lambda _h: {},
    )

    handler._emit_event("NARRATION", payload)

    # Carl is the emitter — his "out_to_self" path must also swap.
    # The emit_event() return value is the message Carl-side sends, BUT
    # in the existing pipeline emitter.out_to_self bypasses the rewriter.
    # Story 49-8 explicitly fixes this: anchor=emitter must still swap.
    # So we assert via the EMITTER queue if it has been wired through
    # the room — otherwise via the return value.
    carl_msgs = []
    while not queues["p_carl"].empty():
        carl_msgs.append(queues["p_carl"].get_nowait())

    # If the emitter sends through the room queue, his frame lands.
    # Otherwise his frame is the return of _emit_event (handler stores
    # it but doesn't route it here). Either way, we want to verify Carl's
    # frame text. So we accept both shapes.
    if carl_msgs:
        carl_text = carl_msgs[0].payload["text"]
    else:
        # emit_event returns the emitter's message in the legacy path.
        # We pull it from the test by running emit_event a second time
        # and capturing the return; OR — the cleaner contract is that
        # in MP, the emitter ALSO receives from the room queue. Story
        # 49-8 must make that contract explicit. We assert here that
        # Carl receives his own frame on the room queue so the swap
        # applies uniformly.
        pytest.fail(
            "Carl (anchor + emitter) received no frame on his queue. "
            "Story 49-8 requires the emitter to receive a swapped frame "
            "through the same fan-out path as peers — otherwise his "
            "own tab shows 'Carl plants a boot' instead of 'You plant'."
        )

    assert "You plant a boot" in carl_text, (
        f"Carl (anchor) must see 2nd-person prose; got: {carl_text!r}"
    )
    assert "Carl plants" not in carl_text, (
        f"Carl must NOT see his own name in 3rd-person; got: {carl_text!r}"
    )


# ---------------------------------------------------------------------------
# 2. Donut + Katia receive 3rd-person unchanged for Carl's card
# ---------------------------------------------------------------------------


def test_non_anchor_recipients_see_third_person_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Donut and Katia must see Carl's card unchanged — 'Carl plants
    a boot...' — because their player_ids do NOT map to anchor_pc.

    This story does NOT filter peer cards (that's ADR-028); all three
    players still receive the card. Only the prose framing differs.
    """
    handler = _make_handler_three_pcs(tmp_path)
    queues = _attach_queues(handler._room)

    from sidequest.server import session_handler as handler_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)

    from sidequest.server import views as views_module

    monkeypatch.setattr(views_module, "status_effects_by_player", lambda _h: {})

    payload = {
        "text": "Carl plants a boot on the moth's thorax.",
        "footnotes": [],
        "_visibility": {
            "visible_to": "all",
            "fidelity": {},
            "anchor_pc": "Carl",
            "pov_strategy": "pc_anchored",
        },
    }
    handler._emit_event("NARRATION", payload)

    # Donut and Katia must each receive exactly one frame, in 3rd-person.
    for pid in ("p_donut", "p_katia"):
        assert queues[pid].qsize() == 1, (
            f"player {pid} expected exactly one NARRATION frame, "
            f"got {queues[pid].qsize()}"
        )
        frame = queues[pid].get_nowait()
        text = frame.payload["text"]
        assert "Carl plants a boot" in text, (
            f"non-anchor recipient {pid} must see 3rd-person; got: {text!r}"
        )
        assert "You plant" not in text, (
            f"non-anchor recipient {pid} must NOT receive the swapped "
            f"prose; got: {text!r}"
        )


# ---------------------------------------------------------------------------
# 3. Atmospheric card (no anchor) goes to everyone unchanged
# ---------------------------------------------------------------------------


def test_atmospheric_card_broadcast_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When pov_strategy=='atmospheric' (no anchor), every recipient
    receives the original prose with no swap. Regression-sentinel for
    setting-only narration."""
    handler = _make_handler_three_pcs(tmp_path)
    queues = _attach_queues(handler._room)

    from sidequest.server import session_handler as handler_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)

    from sidequest.server import views as views_module

    monkeypatch.setattr(views_module, "status_effects_by_player", lambda _h: {})

    canonical = "Rain hammers the slate roof. The corridor smells of wet iron."
    payload = {
        "text": canonical,
        "footnotes": [],
        "_visibility": {
            "visible_to": "all",
            "fidelity": {},
            "anchor_pc": None,
            "pov_strategy": "atmospheric",
        },
    }
    handler._emit_event("NARRATION", payload)

    for pid in ("p_donut", "p_katia"):
        assert queues[pid].qsize() == 1
        frame = queues[pid].get_nowait()
        assert frame.payload["text"] == canonical, (
            f"atmospheric prose must be untouched for {pid}; got: "
            f"{frame.payload['text']!r}"
        )


# ---------------------------------------------------------------------------
# 4. Anchor pronoun-driven swap — she/her case
# ---------------------------------------------------------------------------


def test_anchor_swap_uses_recipient_pc_pronouns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anchored on Katia (she/her), Katia's tab must see 2nd-person.
    Reflexive 'herself' must become 'yourself' on her tab."""
    handler = _make_handler_three_pcs(tmp_path)
    queues = _attach_queues(handler._room)

    from sidequest.server import session_handler as handler_module

    class _FakeMsg:
        def __init__(self, payload):
            self.payload = payload

    monkeypatch.setitem(handler_module._KIND_TO_MESSAGE_CLS, "NARRATION", _FakeMsg)

    from sidequest.server import views as views_module

    monkeypatch.setattr(views_module, "status_effects_by_player", lambda _h: {})

    payload = {
        "text": "Katia braces herself and eases the knife back.",
        "footnotes": [],
        "_visibility": {
            "visible_to": "all",
            "fidelity": {},
            "anchor_pc": "Katia",
            "pov_strategy": "pc_anchored",
        },
    }
    handler._emit_event("NARRATION", payload)

    assert queues["p_katia"].qsize() == 1, "Katia must receive her own card"
    katia_text = queues["p_katia"].get_nowait().payload["text"]
    assert "You brace yourself" in katia_text, (
        f"Katia (anchor, she/her) must see swapped reflexive; got: {katia_text!r}"
    )
    assert "herself" not in katia_text
    assert "Katia" not in katia_text

    # Carl and Donut keep the 3rd-person form.
    for pid in ("p_carl", "p_donut"):
        text = queues[pid].get_nowait().payload["text"]
        assert "Katia braces herself" in text, (
            f"non-anchor {pid} must see 3rd-person; got: {text!r}"
        )
