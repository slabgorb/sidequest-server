"""Vessel-tag parser — extract composure / composure_max from inventory tags.

Story 53-2, Epic 53 (Road Warrior). Content-side vessel items encode
mechanical pool state as colon-separated tag strings on the item dict:

    {"id": "rig_tier_1_prospect",
     "tags": ["vessel", "rig", "tier-1", "composure:4", "composure_max:4", ...]}

The materializer-binding helper (story 53-2) reads these tags at instantiation
time to build a :class:`RigComposurePool`. This module exists so the parser
can be exercised independently of the binding flow and so the negative paths
(missing tag, non-integer, mismatched bounds) fail loud per CLAUDE.md
"No Silent Fallbacks".

These tests are RED until Dev implements ``sidequest.game.vessel_tags``.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Import wiring — the parser must be reachable from a stable production path.
# ---------------------------------------------------------------------------


def test_parse_vessel_tags_importable_from_game_package() -> None:
    """Production code paths must reach the parser via ``sidequest.game``.

    Per CLAUDE.md "Every Test Suite Needs a Wiring Test": fix the import
    surface in tests, let Dev choose the file location.
    """
    from sidequest.game import parse_vessel_tags  # noqa: F401


def test_vessel_tags_dataclass_importable_from_game_package() -> None:
    """The typed result object is part of the public API surface."""
    from sidequest.game import VesselTags  # noqa: F401


def test_invalid_vessel_tags_error_importable_from_game_package() -> None:
    """The custom exception type is public so callers can catch precisely.

    A bare ``ValueError`` would swallow into pydantic ``ValidationError``
    upstream and lose the "which item" context.
    """
    from sidequest.game import InvalidVesselTagsError  # noqa: F401


# ---------------------------------------------------------------------------
# Happy path — well-formed vessel items parse cleanly.
# ---------------------------------------------------------------------------


def _vessel_item(
    *,
    item_id: str = "rig_tier_1_prospect",
    composure: int | str = 4,
    composure_max: int | str = 4,
    extra_tags: list[str] | None = None,
) -> dict:
    """Build a content-shape inventory item dict with composure tags.

    Mirrors the catalog dict produced by
    ``sidequest.server.dispatch.chargen_loadout._item_dict_from_catalog`` —
    the production source of inventory items.
    """
    tags = [
        "vessel",
        "rig",
        "tier-1",
        f"composure:{composure}",
        f"composure_max:{composure_max}",
        "speed:3",
        "armor:0",
        "mount_slots:1",
    ]
    if extra_tags:
        tags.extend(extra_tags)
    return {
        "id": item_id,
        "name": "Prospect Rig",
        "category": "vessel",
        "tags": tags,
    }


def test_parse_vessel_tags_extracts_composure_and_max() -> None:
    """A well-formed vessel item yields a VesselTags with both fields populated."""
    from sidequest.game import parse_vessel_tags

    result = parse_vessel_tags(_vessel_item(composure=4, composure_max=4))

    assert result.composure == 4
    assert result.composure_max == 4


def test_parse_vessel_tags_handles_partial_damage() -> None:
    """Composure below max is preserved verbatim (pool currently damaged)."""
    from sidequest.game import parse_vessel_tags

    result = parse_vessel_tags(_vessel_item(composure=2, composure_max=6))

    assert result.composure == 2
    assert result.composure_max == 6


def test_parse_vessel_tags_ignores_non_composure_tags() -> None:
    """speed / armor / fuel / mount_slots tags are inert for 53-2.

    Story scope intentionally defers parsing those — 53-2 only wires the
    composure pool. Adding more tags must not break the parser.
    """
    from sidequest.game import parse_vessel_tags

    item = _vessel_item(extra_tags=["fuel_capacity:60", "armor_plate", "lore:salvaged"])
    result = parse_vessel_tags(item)

    assert result.composure == 4
    assert result.composure_max == 4


# ---------------------------------------------------------------------------
# Negative paths — every malformed shape MUST raise loudly. No fallbacks.
# ---------------------------------------------------------------------------


def test_parse_vessel_tags_rejects_missing_composure_tag() -> None:
    """No ``composure:N`` tag → loud failure with the item id in the message."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_broken_no_composure",
        "name": "Bad Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError) as exc_info:
        parse_vessel_tags(item)

    assert "composure" in str(exc_info.value).lower()
    assert "rig_broken_no_composure" in str(exc_info.value)


def test_parse_vessel_tags_rejects_missing_composure_max_tag() -> None:
    """No ``composure_max:N`` tag → loud failure with the item id."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_broken_no_max",
        "name": "Bad Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:4"],
    }
    with pytest.raises(InvalidVesselTagsError) as exc_info:
        parse_vessel_tags(item)

    assert "composure_max" in str(exc_info.value).lower()
    assert "rig_broken_no_max" in str(exc_info.value)


def test_parse_vessel_tags_rejects_non_integer_composure() -> None:
    """``composure:foo`` is a content typo — fail loud, do not coerce to 0."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_typo",
        "name": "Typo Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:foo", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_negative_composure() -> None:
    """Negative composure is not a valid runtime state — fail at parse time."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_negative",
        "name": "Cursed Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:-1", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_zero_or_negative_max() -> None:
    """A born-dead rig (max <= 0) must not enter game state."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_born_dead",
        "name": "Wreck",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:0", "composure_max:0"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_composure_above_max() -> None:
    """``composure > composure_max`` is a content bug — fail early.

    The pool model already enforces this (story 53-1's ``_check_bounds``),
    but failing at parse time produces a friendlier error pointing at the
    *item*, not the pool ctor.
    """
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_inflated",
        "name": "Inflated Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:9", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError) as exc_info:
        parse_vessel_tags(item)

    assert "rig_inflated" in str(exc_info.value)


def test_parse_vessel_tags_rejects_duplicate_composure_tag() -> None:
    """Two ``composure:N`` tags → content bug, do not silently pick one."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "rig_dupe",
        "name": "Duplicate Rig",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:4", "composure:3", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_non_vessel_item() -> None:
    """Caller bug: passing a non-vessel item is a programming error.

    The materializer's ``find_vessel_item`` is responsible for the
    pre-filter; if a non-vessel item reaches the parser, fail loud.
    """
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "id": "tool_kit",
        "name": "Tool Kit",
        "category": "tool",
        "tags": ["tool", "kit"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_empty_tags() -> None:
    """Item with no tags at all → loud failure (likely a malformed catalog entry)."""
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {"id": "rig_empty", "name": "Empty", "category": "vessel", "tags": []}
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)


def test_parse_vessel_tags_rejects_missing_id() -> None:
    """An item dict without ``id`` cannot bind a pool — fail loud.

    Without an id the binding would have no ``chassis_id`` to use, and a
    silent fallback (synthesizing one from name) is exactly the kind of
    thing that masks content bugs.
    """
    from sidequest.game import InvalidVesselTagsError, parse_vessel_tags

    item = {
        "name": "Nameless",
        "category": "vessel",
        "tags": ["vessel", "rig", "composure:4", "composure_max:4"],
    }
    with pytest.raises(InvalidVesselTagsError):
        parse_vessel_tags(item)
