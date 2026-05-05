"""Tests for sidequest.game.delve_lifecycle.

Sünden engine plan item 4a — pure-functions module covering:

- ``is_hub_world`` — single source of truth for the hub-vs-leaf check.
- ``materialize_party`` — copy roster identity into Character shapes
  with ``hireling_id`` linkage; raise on bad input.
- ``commit_back`` — task 6.
- ``apply_delve_end`` — task 6.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sidequest.game.character import Character
from sidequest.game.creature_core import (
    CreatureCore,
    Inventory,
    placeholder_edge_pool,
)
from sidequest.game.delve_lifecycle import (
    apply_delve_end,
    commit_back,
    is_hub_world,
    materialize_party,
)
from sidequest.game.session import GameSnapshot
from sidequest.game.world_save import Hireling, WorldSave


def _h(id_: str, status: str = "active") -> Hireling:
    """Roster-row factory: id-derived display name, default archetype.

    NOTE: Hireling.id has a slug pattern (``^[a-z][a-z0-9_]+$``,
    minimum two chars) locked in by prior plan task 2. Single-letter
    ids in the plan spec (``_h("a")``) predate that constraint —
    callers here must pass ``"a_1"``-shaped ids.
    """
    return Hireling(id=id_, name=id_.title(), archetype="prig", status=status)


def _make_character(
    *,
    name: str,
    hireling_id: str | None = None,
    is_dead: bool = False,
) -> Character:
    """Minimal Character for commit_back / apply_delve_end coverage."""
    return Character(
        core=CreatureCore(
            name=name,
            description="placeholder",
            personality="placeholder",
            inventory=Inventory(),
            statuses=[],
            edge=placeholder_edge_pool(),
        ),
        backstory="placeholder backstory",
        char_class="Fighter",
        race="Human",
        hireling_id=hireling_id,
        is_dead=is_dead,
    )


# ---------------------------------------------------------------------------
# is_hub_world
# ---------------------------------------------------------------------------


def test_is_hub_world_true_when_dungeons():
    """A hub world (dungeons populated) returns True."""
    from sidequest.genre.loader import load_genre_pack_cached

    pack = load_genre_pack_cached("caverns_and_claudes")
    assert is_hub_world(pack.worlds["caverns_three_sins"]) is True


def test_is_hub_world_false_for_leaf():
    """A leaf world (cartography only, no dungeons) returns False."""
    from sidequest.genre.loader import load_genre_pack_cached

    pack = load_genre_pack_cached("space_opera")
    assert is_hub_world(pack.worlds["coyote_star"]) is False


# ---------------------------------------------------------------------------
# materialize_party — validation
# ---------------------------------------------------------------------------


def test_materialize_party_validates_size_lower():
    with pytest.raises(ValueError, match="party size"):
        materialize_party([_h("a_1")], [], world_slug="x", dungeon=...)  # type: ignore[arg-type]


def test_materialize_party_validates_size_upper():
    roster = [_h(f"h_{i}") for i in range(7)]
    with pytest.raises(ValueError, match="party size"):
        materialize_party(
            roster,
            [h.id for h in roster],
            world_slug="x",
            dungeon=...,  # type: ignore[arg-type]
        )


def test_materialize_party_rejects_missing_id():
    with pytest.raises(ValueError, match="not in roster"):
        materialize_party(
            [_h("a_1")], ["a_1", "b_1"], world_slug="x", dungeon=...,  # type: ignore[arg-type]
        )


def test_materialize_party_rejects_dead_hireling():
    with pytest.raises(ValueError, match="not active"):
        materialize_party(
            [_h("a_1"), _h("b_1", status="dead")],
            ["a_1", "b_1"],
            world_slug="x",
            dungeon=...,  # type: ignore[arg-type]
        )


def test_materialize_party_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicates"):
        materialize_party(
            [_h("a_1"), _h("b_1")],
            ["a_1", "a_1"],
            world_slug="x",
            dungeon=...,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# materialize_party — happy path
# ---------------------------------------------------------------------------


def test_materialize_party_carries_hireling_id_and_name():
    """Plan deviation, logged loudly: the plan asserts ``ch.core.archetype``,
    but ``CreatureCore`` is ``extra="forbid"`` and has no ``archetype``
    field. Adding one would be a load-bearing schema change for an
    aspirational test in §5; instead the materializer sets the
    existing ``Character.resolved_archetype`` (P2-deferred chargen
    axis) which is the correct home for a resolved archetype slug.
    """
    from sidequest.genre.loader import load_genre_pack_cached

    pack = load_genre_pack_cached("caverns_and_claudes")
    dungeon = pack.worlds["caverns_three_sins"].dungeons["grimvault"]
    roster = [_h("vol_1"), _h("zin_1")]
    party = materialize_party(
        roster,
        ["vol_1", "zin_1"],
        world_slug="caverns_three_sins",
        dungeon=dungeon,
    )
    assert len(party) == 2
    assert {ch.core.name for ch in party} == {"Vol_1", "Zin_1"}
    assert {ch.resolved_archetype for ch in party} == {"prig"}
    # Commit-back attribution match key — must round-trip.
    assert {ch.hireling_id for ch in party} == {"vol_1", "zin_1"}


def test_materialize_party_does_not_carry_stress():
    """Stress lives on Hireling but is item 3 territory; this plan does
    not propagate it to Character. Defensive test so a future change
    that adds stress propagation gets caught and routed to item 3."""
    from sidequest.genre.loader import load_genre_pack_cached

    pack = load_genre_pack_cached("caverns_and_claudes")
    dungeon = pack.worlds["caverns_three_sins"].dungeons["grimvault"]
    h = Hireling(id="stressed_1", name="X", archetype="prig", stress=42)
    [ch] = materialize_party(
        [h],
        ["stressed_1"],
        world_slug="caverns_three_sins",
        dungeon=dungeon,
    )
    # Item 3 will add Character.stress; until then, the field either
    # doesn't exist on Character or stays at default. Explicit absence
    # check via model_dump avoids accidental copy.
    dump = ch.model_dump()
    assert "stress" not in dump or dump.get("stress") == 0


# ---------------------------------------------------------------------------
# commit_back
# ---------------------------------------------------------------------------


def test_commit_back_matches_by_id_not_name():
    """Two hirelings with the same display name — namegen collision is
    a real possibility on a small culture corpus. Commit-back MUST
    attribute death to the right id, not the first name match."""
    ws = WorldSave(
        roster=[
            _h("a_1"),
            _h("b_1"),  # default _h sets name = id.title()
            Hireling(id="dup_1", name="Volga", archetype="prig"),
            Hireling(id="dup_2", name="Volga", archetype="prig"),  # same name
        ]
    )
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [
        _make_character(name="Volga", hireling_id="dup_1", is_dead=False),
        _make_character(name="Volga", hireling_id="dup_2", is_dead=True),
    ]
    new_ws = commit_back(snap, ws)
    by_id = {h.id: h for h in new_ws.roster}
    assert by_id["dup_1"].status == "active"
    assert by_id["dup_2"].status == "dead"


def test_commit_back_copies_dead_status():
    ws = WorldSave(roster=[_h("a_1"), _h("b_1")])
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [
        _make_character(name="A", hireling_id="a_1", is_dead=False),
        _make_character(name="B", hireling_id="b_1", is_dead=True),
    ]
    new_ws = commit_back(snap, ws)
    by_id = {h.id: h for h in new_ws.roster}
    assert by_id["a_1"].status == "active"
    assert by_id["b_1"].status == "dead"


def test_commit_back_ignores_character_without_hireling_id():
    """Legacy chargen-spawned PC has hireling_id=None — commit-back
    skips it (no roster row to write back to). Doesn't crash."""
    ws = WorldSave(roster=[_h("a_1")])
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [
        _make_character(name="LegacyPC", hireling_id=None, is_dead=True),
        _make_character(name="A", hireling_id="a_1", is_dead=True),
    ]
    new_ws = commit_back(snap, ws)
    assert {h.id: h.status for h in new_ws.roster} == {"a_1": "dead"}


def test_commit_back_does_not_touch_stress():
    """Stress is item 3 territory. commit_back must NOT propagate
    Character.stress to Hireling.stress in this plan — even if a
    future Character.stress field exists."""
    ws = WorldSave(
        roster=[
            Hireling(id="a_1", name="A", archetype="prig", stress=42),
        ]
    )
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    # _make_character may or may not have a stress field after item 3;
    # either way, commit_back leaves the Hireling stress untouched.
    snap.characters = [_make_character(name="A", hireling_id="a_1")]
    new_ws = commit_back(snap, ws)
    assert new_ws.roster[0].stress == 42


# ---------------------------------------------------------------------------
# apply_delve_end
# ---------------------------------------------------------------------------


def test_apply_delve_end_increments_count_and_appends_wall():
    ws = WorldSave(
        roster=[_h("a_1"), _h("b_1")],
        delve_count=2,
    )
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [
        _make_character(name="A", hireling_id="a_1"),
        _make_character(name="B", hireling_id="b_1"),
    ]

    new_ws = apply_delve_end(
        ws,
        dungeon_slug="grimvault",
        dungeon_sin="pride",
        outcome="victory",
        wounded_boss=False,
        party_hireling_ids=["a_1", "b_1"],
        snapshot=snap,
        timestamp=datetime(2026, 5, 5, tzinfo=UTC),
    )
    assert new_ws.delve_count == 3
    assert new_ws.latest_delve_sin == "pride"
    assert len(new_ws.wall) == 1
    assert new_ws.wall[0].outcome == "victory"
    assert new_ws.wall[0].wounded_boss is False
    assert new_ws.wall[0].dungeon == "grimvault"
    assert new_ws.wall[0].delve_number == 3
    assert new_ws.dungeon_wounds == {}  # no wound flag on non-wounded delve


def test_apply_delve_end_wound_flag_orthogonal_to_outcome():
    """Spec §"Wounded Sins" line 81: "a successful boss delve flips a
    permanent flag." The flag flips when wounded_boss=True, regardless
    of outcome — so a TPK-after-wound is recordable correctly."""
    ws = WorldSave(roster=[_h("a_1")])
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [_make_character(name="A", hireling_id="a_1", is_dead=True)]
    new_ws = apply_delve_end(
        ws,
        dungeon_slug="grimvault",
        dungeon_sin="pride",
        outcome="defeat",
        wounded_boss=True,
        party_hireling_ids=["a_1"],
        snapshot=snap,
        timestamp=datetime.now(tz=UTC),
    )
    assert new_ws.dungeon_wounds == {"grimvault": True}
    assert new_ws.wall[0].outcome == "defeat"
    assert new_ws.wall[0].wounded_boss is True
    by_id = {h.id: h for h in new_ws.roster}
    assert by_id["a_1"].status == "dead"  # commit-back honored


def test_apply_delve_end_defeat_without_wound_does_not_wound():
    ws = WorldSave(roster=[_h("a_1")])
    snap = GameSnapshot(genre_slug="x", world_slug="y")
    snap.characters = [_make_character(name="A", hireling_id="a_1", is_dead=True)]
    new_ws = apply_delve_end(
        ws,
        dungeon_slug="grimvault",
        dungeon_sin="pride",
        outcome="defeat",
        wounded_boss=False,
        party_hireling_ids=["a_1"],
        snapshot=snap,
        timestamp=datetime.now(tz=UTC),
    )
    assert new_ws.dungeon_wounds == {}  # not wounded
    assert new_ws.wall[0].wounded_boss is False
