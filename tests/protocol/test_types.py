"""Tests for NonBlankString and Stat newtypes.

Ported from sidequest-protocol/src/tests.rs (newtype_tests module)
and sidequest-protocol/src/types.rs (unit_tests module).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.protocol.types import NonBlankString, Stat

# ---------------------------------------------------------------------------
# NonBlankString — from tests.rs newtype_tests
# ---------------------------------------------------------------------------


def test_non_blank_string_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        NonBlankString("")


def test_non_blank_string_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        NonBlankString("   ")


def test_non_blank_string_accepts_valid_text() -> None:
    nbs = NonBlankString("hello")
    assert nbs.as_str() == "hello"


def test_non_blank_string_trims_whitespace() -> None:
    nbs = NonBlankString("  hello  ")
    assert nbs.as_str() == "hello"


def test_non_blank_string_deserialize_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        NonBlankString.model_validate_json('""')


def test_non_blank_string_deserialize_accepts_valid() -> None:
    nbs = NonBlankString.model_validate_json('"hello"')
    assert nbs.as_str() == "hello"


def test_non_blank_string_serializes_as_plain_string() -> None:
    nbs = NonBlankString("hello")
    assert nbs.model_dump_json() == '"hello"'


# ---------------------------------------------------------------------------
# NonBlankString — from types.rs unit_tests
# ---------------------------------------------------------------------------


def test_display_shows_inner_value() -> None:
    nbs = NonBlankString("hello")
    assert str(nbs) == "hello"


# ---------------------------------------------------------------------------
# Stat — from types.rs unit_tests
# ---------------------------------------------------------------------------


def test_stat_canonicalizes_to_uppercase() -> None:
    assert Stat("strength").as_str() == "STRENGTH"
    assert Stat("Strength").as_str() == "STRENGTH"
    assert Stat("STRENGTH").as_str() == "STRENGTH"
    assert Stat("  influence  ").as_str() == "INFLUENCE"


def test_stat_equal_across_casing() -> None:
    a = Stat("Influence")
    b = Stat("INFLUENCE")
    c = Stat("influence")
    assert a == b
    assert b == c


def test_stat_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        Stat("")
    with pytest.raises(ValidationError):
        Stat("   ")
    with pytest.raises(ValidationError):
        Stat("\t\n")


def test_stat_roundtrips_through_json_canonical() -> None:
    stat = Stat("Influence")
    json_str = stat.model_dump_json()
    assert json_str == '"INFLUENCE"'
    back = Stat.model_validate_json(json_str)
    assert back == stat


def test_stat_deserializes_case_insensitively() -> None:
    a = Stat.model_validate_json('"NERVE"')
    b = Stat.model_validate_json('"Nerve"')
    c = Stat.model_validate_json('"nerve"')
    assert a == b
    assert b == c
    assert a.as_str() == "NERVE"


def test_stat_deserialize_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        Stat.model_validate_json('""')
    with pytest.raises(ValidationError):
        Stat.model_validate_json('"   "')
