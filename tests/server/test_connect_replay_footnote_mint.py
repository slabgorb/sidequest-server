"""Replay/backfill footnote fact_id mint — sq-playtest 2026-05-16.

Carried fix #4 added a defensive ``fact_id`` mint to the *live* footnote-
forwarding loop only. Resuming a save written *before* that fix replays
NARRATION footnotes verbatim with ``fact_id=None``; the client
(``useStateMirror.ts``) drops every footnote lacking a fact_id, so the
load-bearing scene facts vanish from the Knowledge journal on every
rehydration of an old save.

``_mint_replay_footnote_fact_ids`` mirrors the live mint on the replay
path. The dedupe-critical property: a replayed pre-fix fact and its later
live re-narration MUST collide on the same id, so the digest here has to
stay byte-identical to the canonical algorithm in
``websocket_session_handler.py``. ``test_replay_minted_id_matches_live_path_digest``
is the regression guard for that contract — if either copy drifts, the
journal double-counts and this test fails.
"""

from __future__ import annotations

import hashlib

from sidequest.handlers.connect import _mint_replay_footnote_fact_ids
from sidequest.protocol.messages import NarrationMessage, NarrationPayload
from sidequest.protocol.models import FactCategory, Footnote


def _live_path_expected_id(summary: str, category: FactCategory, is_new: bool) -> str:
    """Independently reproduce the canonical live-path digest (carried #4,
    ``websocket_session_handler.py``). If this formula and the helper ever
    disagree, replayed facts stop colliding with live re-narrations and the
    journal duplicates — the whole reason the mint is deterministic.
    """
    cat_str = category.value if hasattr(category, "value") else str(category)
    digest = hashlib.sha256(f"{summary}|{cat_str}|{is_new}".encode()).hexdigest()
    return f"fn-{digest[:16]}"


def _narration(*footnotes: Footnote) -> NarrationMessage:
    return NarrationMessage(
        payload=NarrationPayload(text="The ledger was scraped clean.", footnotes=list(footnotes))
    )


def test_mints_deterministic_id_when_fact_id_missing() -> None:
    fn = Footnote(
        summary="The Confraternity holds the bond on the bronze fitting.",
        category=FactCategory.Lore,
        is_new=True,
    )
    msg = _narration(fn)

    rebuilt, minted = _mint_replay_footnote_fact_ids(msg)

    assert minted == 1
    out_fn = rebuilt.payload.footnotes[0]
    assert out_fn.fact_id is not None
    assert out_fn.fact_id.startswith("fn-")
    # Original is left untouched (model_copy, not mutation).
    assert msg.payload.footnotes[0].fact_id is None


def test_replay_minted_id_matches_live_path_digest() -> None:
    """Dedupe contract: replay-minted id == live-path-minted id for the
    same (summary, category, is_new). This is the load-bearing guarantee —
    without it, resuming an old save then re-narrating the same fact would
    enter the journal twice."""
    summary = "Old Marrow died three nights ago; his name is now on the Wall."
    fn = Footnote(summary=summary, category=FactCategory.Person, is_new=True)

    rebuilt, minted = _mint_replay_footnote_fact_ids(_narration(fn))

    assert minted == 1
    assert rebuilt.payload.footnotes[0].fact_id == _live_path_expected_id(
        summary, FactCategory.Person, True
    )


def test_preserves_narrator_supplied_fact_id() -> None:
    """Scenario clue_intake Seam A matches narrator-supplied fact_ids
    against genre-authored ClueNode.id — replacing them would break it."""
    fn = Footnote(
        summary="The courier reads lips.",
        category=FactCategory.Person,
        is_new=True,
        fact_id="clue-courier-reads-lips",
    )
    msg = _narration(fn)

    rebuilt, minted = _mint_replay_footnote_fact_ids(msg)

    assert minted == 0
    assert rebuilt is msg  # unchanged object — no needless copy
    assert rebuilt.payload.footnotes[0].fact_id == "clue-courier-reads-lips"


def test_mixed_footnotes_mint_only_the_missing_ones() -> None:
    supplied = Footnote(
        summary="Hesh is the Confraternity signatory at Ashgate.",
        category=FactCategory.Person,
        is_new=True,
        fact_id="clue-hesh-signatory",
    )
    missing = Footnote(
        summary="The Downriver Courier loiters at the saffron threshold.",
        category=FactCategory.Place,
        is_new=True,
    )

    rebuilt, minted = _mint_replay_footnote_fact_ids(_narration(supplied, missing))

    assert minted == 1
    assert rebuilt.payload.footnotes[0].fact_id == "clue-hesh-signatory"
    assert rebuilt.payload.footnotes[1].fact_id == _live_path_expected_id(
        missing.summary, FactCategory.Place, True
    )


def test_noop_for_non_narration_message() -> None:
    class _NotNarration:
        type = "PARTY_STATUS"

    sentinel = _NotNarration()
    out, minted = _mint_replay_footnote_fact_ids(sentinel)

    assert minted == 0
    assert out is sentinel


def test_noop_for_footnote_free_narration() -> None:
    msg = _narration()
    out, minted = _mint_replay_footnote_fact_ids(msg)

    assert minted == 0
    assert out is msg


# --- Wiring: the helper is called from the production replay path ---------


def test_helper_is_wired_into_connect_replay_path() -> None:
    """CLAUDE.md: every test suite needs a wiring test proving the unit is
    reachable from production code, not just correct in isolation. The mint
    is worthless if it is defined but never invoked on the replay path.

    Asserts the helper is invoked at each of the three replay emission
    sites — the cached-rows loop, the legacy live-filter fallback, and the
    tail-backfill — all inside the slug_connect replay function (anchored
    after the ``replay_msgs`` accumulator is created).
    """
    import inspect

    import sidequest.handlers.connect as connect_mod

    src = inspect.getsource(connect_mod)
    assert "def _mint_replay_footnote_fact_ids(" in src

    body = src.split("replay_msgs: list[object] = []", 1)
    assert len(body) == 2, "replay accumulator marker not found — test anchor stale"
    replay_region = body[1]
    call_sites = replay_region.count("_mint_replay_footnote_fact_ids(")
    assert call_sites >= 3, (
        f"expected the mint at all 3 replay emission sites (cached loop, "
        f"legacy fallback, tail-backfill); found {call_sites}"
    )
    # And the GM-panel visibility span mirrors the live event name with the
    # replay-distinct reason (ADR-100 Seam C observability).
    assert '"state.footnote_fact_id_minted"' in replay_region
    assert '"reason": "replay_backfill"' in replay_region
