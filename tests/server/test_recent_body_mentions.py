"""Recent-body-mentions ring buffer on Session.

Plot-a-course MVP buffer: simple deque of last 4 distinct body IDs
mentioned by narrator output or player input. Populated by
``Session.note_body_mentioned`` after each turn's narration is
applied. Read by ``Orchestrator`` when assembling the <courses> block.
"""
from __future__ import annotations

from sidequest.orbital.loader import OrbitalContent
from sidequest.orbital.models import (
    BodyDef,
    BodyType,
    ClockConfig,
    OrbitsConfig,
    TravelConfig,
    TravelRealism,
)


def _content() -> OrbitalContent:
    return OrbitalContent(
        orbits=OrbitsConfig(
            version="0.1.0",
            clock=ClockConfig(),
            travel=TravelConfig(realism=TravelRealism.ORBITAL),
            bodies={
                "coyote": BodyDef(type=BodyType.STAR),
                "alpha": BodyDef(
                    type=BodyType.HABITAT,
                    parent="coyote",
                    semi_major_au=1.0,
                    period_days=365.0,
                    epoch_phase_deg=0.0,
                ),
                "beta": BodyDef(
                    type=BodyType.HABITAT,
                    parent="coyote",
                    semi_major_au=2.0,
                    period_days=720.0,
                    epoch_phase_deg=90.0,
                ),
            },
        ),
        chart=None,
    )


def _session():
    from sidequest.game.session import GameSnapshot
    from sidequest.server.session import Session

    return Session(GameSnapshot(), orbital_content=_content())


def test_recent_body_mentions_starts_empty() -> None:
    sess = _session()
    assert list(sess.recent_body_mentions) == []


def test_note_body_mentioned_appends() -> None:
    sess = _session()
    sess.note_body_mentioned("alpha")
    sess.note_body_mentioned("beta")
    assert list(sess.recent_body_mentions) == ["alpha", "beta"]


def test_recent_body_mentions_caps_at_4() -> None:
    sess = _session()
    sess.note_body_mentioned("a")
    sess.note_body_mentioned("b")
    sess.note_body_mentioned("c")
    sess.note_body_mentioned("d")
    sess.note_body_mentioned("e")
    # oldest 'a' evicted
    assert list(sess.recent_body_mentions) == ["b", "c", "d", "e"]


def test_note_body_mentioned_dedupe_moves_to_recent() -> None:
    """Re-mentioning a body refreshes its position to the most-recent
    end, so it survives subsequent evictions. Without this, a body the
    player keeps talking about would still drop off after 4 distinct
    mentions of other bodies."""
    sess = _session()
    sess.note_body_mentioned("a")
    sess.note_body_mentioned("b")
    sess.note_body_mentioned("a")  # refreshed to end
    sess.note_body_mentioned("c")
    sess.note_body_mentioned("d")
    sess.note_body_mentioned("e")
    # 'a' was refreshed before 'b' was old, so 'b' evicts; 'a' survives.
    assert "a" in sess.recent_body_mentions
    assert "b" not in sess.recent_body_mentions
