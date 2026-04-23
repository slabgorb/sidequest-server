"""Field-path read/write with dotted + [*] wildcards."""
from __future__ import annotations

from sidequest.game.projection.field_path import apply_mask, read_path


def test_read_flat_field() -> None:
    assert read_path({"hp": 10}, "hp") == [10]


def test_read_dotted_field() -> None:
    assert read_path({"target": {"hp": 10}}, "target.hp") == [10]


def test_read_wildcard_list() -> None:
    payload = {"enemies": [{"position": "A"}, {"position": "B"}]}
    assert read_path(payload, "enemies[*].position") == ["A", "B"]


def test_read_missing_path_returns_empty_list() -> None:
    assert read_path({"hp": 10}, "mp") == []


def test_apply_mask_flat() -> None:
    payload = {"hp": 10}
    apply_mask(payload, "hp", mask="??")
    assert payload == {"hp": "??"}


def test_apply_mask_dotted() -> None:
    payload = {"target": {"hp": 10}}
    apply_mask(payload, "target.hp", mask="??")
    assert payload == {"target": {"hp": "??"}}


def test_apply_mask_wildcard() -> None:
    payload = {"enemies": [{"position": "A"}, {"position": "B"}]}
    apply_mask(payload, "enemies[*].position", mask=None)
    assert payload == {"enemies": [{"position": None}, {"position": None}]}


def test_apply_mask_missing_path_is_noop() -> None:
    payload = {"hp": 10}
    apply_mask(payload, "mp", mask=0)
    assert payload == {"hp": 10}
