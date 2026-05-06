"""Failing tests for ``GameSnapshot.party_location()`` (story 45-48 / Wave 2B / S3).

Per design doc 2026-05-04-snapshot-split-brain-cleanup-design.md §"Wave 2 Story B"
(pp. 203-238), the party-level ``snapshot.location`` field is removed and a
computed accessor takes its place. The accessor has three modes:

1. ``perspective`` supplied — returns that character's last-known location
   (``character_locations[perspective]``) or ``None`` if unknown.
2. ``perspective`` omitted, all seated PCs agree on a location — returns the
   consensus string.
3. ``perspective`` omitted, seated PCs disagree (or none seated) — returns
   ``None`` (party split / no party).

Single source of truth: ``snapshot.character_locations``. The legacy
``snapshot.location`` field is removed; this accessor is the *only* way to
ask "where is the party" without a per-character key.

Spec source: docs/superpowers/specs/2026-05-04-snapshot-split-brain-cleanup-design.md
"""

from __future__ import annotations

import pytest

from sidequest.game.session import GameSnapshot


# ---------------------------------------------------------------------------
# Mode 1: perspective supplied — single-player narrator framing
# ---------------------------------------------------------------------------


def test_party_location_with_perspective_returns_that_characters_location() -> None:
    snap = GameSnapshot(
        character_locations={"Shirley": "Cockpit", "Laverne": "Galley"},
    )
    assert snap.party_location(perspective="Shirley") == "Cockpit"
    assert snap.party_location(perspective="Laverne") == "Galley"


def test_party_location_with_unknown_perspective_returns_none() -> None:
    """Asking for a character with no entry returns None — not a fallback to
    consensus, not an empty string. Caller decides how to render 'unknown'."""
    snap = GameSnapshot(character_locations={"Shirley": "Cockpit"})
    assert snap.party_location(perspective="Laverne") is None


def test_party_location_with_perspective_against_empty_locations_is_none() -> None:
    snap = GameSnapshot(character_locations={})
    assert snap.party_location(perspective="Anyone") is None


# ---------------------------------------------------------------------------
# Mode 2: no perspective — consensus across seated PCs
# ---------------------------------------------------------------------------


def test_party_location_consensus_returns_common_string_when_all_agree() -> None:
    """When every seated PC has the same location, that's the consensus."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Galley", "Laverne": "Galley"},
    )
    assert snap.party_location() == "Galley"


def test_party_location_consensus_with_single_seated_pc_returns_their_location() -> None:
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley"},
        character_locations={"Shirley": "Cockpit"},
    )
    assert snap.party_location() == "Cockpit"


# ---------------------------------------------------------------------------
# Mode 3: no perspective + party split — None
# ---------------------------------------------------------------------------


def test_party_location_returns_none_when_seated_pcs_disagree() -> None:
    """The literal split-brain case the design names: peers in different rooms."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Cockpit", "Laverne": "Galley"},
    )
    assert snap.party_location() is None


def test_party_location_returns_none_when_no_seated_pcs_have_locations() -> None:
    """Seated PCs exist but no character_locations entries — party not yet placed."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley"},
        character_locations={},
    )
    assert snap.party_location() is None


def test_party_location_returns_none_when_some_seated_pcs_lack_entries() -> None:
    """Partial coverage is split-brain by definition — one PC unknown means
    we cannot claim consensus."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Cockpit"},  # Laverne missing
    )
    assert snap.party_location() is None


def test_party_location_returns_none_with_no_seated_pcs() -> None:
    """No party seated — pre-chargen / fresh session. Empty agreement is not
    consensus; return None."""
    snap = GameSnapshot(player_seats={}, character_locations={})
    assert snap.party_location() is None


# ---------------------------------------------------------------------------
# Precedence: perspective wins over consensus
# ---------------------------------------------------------------------------


def test_party_location_perspective_overrides_consensus() -> None:
    """Even if the party agrees, perspective returns that character's value
    (which equals consensus when they agree, but the precedence is explicit
    so a future drift is always answerable)."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Galley", "Laverne": "Galley"},
    )
    assert snap.party_location(perspective="Shirley") == "Galley"


def test_party_location_perspective_returns_known_value_even_in_split() -> None:
    """When the party is split AND perspective is supplied, return that
    character's known location — perspective bypasses the consensus check."""
    snap = GameSnapshot(
        player_seats={"p:1": "Shirley", "p:2": "Laverne"},
        character_locations={"Shirley": "Cockpit", "Laverne": "Galley"},
    )
    assert snap.party_location(perspective="Shirley") == "Cockpit"
    assert snap.party_location(perspective="Laverne") == "Galley"


# ---------------------------------------------------------------------------
# AC1: snapshot.location field is removed (no longer settable)
# ---------------------------------------------------------------------------


def test_snapshot_no_longer_exposes_party_level_location_attribute() -> None:
    """AC1 — ``snapshot.location`` is removed from GameSnapshot. The field
    should not appear on a freshly constructed instance."""
    snap = GameSnapshot()
    assert not hasattr(snap, "location"), (
        "snapshot.location should be removed (Wave 2B AC1) — use "
        "snapshot.party_location(perspective=...) or snapshot.character_locations[name]"
    )


def test_snapshot_location_kwarg_is_silently_dropped_or_rejected() -> None:
    """Passing ``location=`` to the constructor must not silently land on a
    persistent attribute. Either pydantic ignores the kwarg (extra='ignore')
    or it errors — both are acceptable; the contract is that no per-instance
    ``location`` attribute survives construction."""
    try:
        snap = GameSnapshot(location="Galley")  # type: ignore[call-arg]
    except Exception:
        return  # rejection is acceptable
    assert not hasattr(snap, "location"), (
        "GameSnapshot(location=...) should not yield a settable attribute"
    )


# ---------------------------------------------------------------------------
# AC2: character_locations remains canonical
# ---------------------------------------------------------------------------


def test_character_locations_field_still_present_and_writable() -> None:
    """AC2 — ``character_locations: dict[str, str]`` remains the canonical
    per-character store; this story does not remove it."""
    snap = GameSnapshot()
    assert hasattr(snap, "character_locations")
    assert isinstance(snap.character_locations, dict)
    snap.character_locations["Shirley"] = "Cockpit"
    assert snap.character_locations["Shirley"] == "Cockpit"
