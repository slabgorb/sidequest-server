"""Tests for GenreCode newtype."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from sidequest.genre.genre_code import GenreCode


def test_valid_simple() -> None:
    assert GenreCode._validate("caverns_and_claudes") == "caverns_and_claudes"


def test_valid_no_underscores() -> None:
    assert GenreCode._validate("lowfantasy") == "lowfantasy"


def test_valid_single_char() -> None:
    assert GenreCode._validate("a") == "a"


def test_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        GenreCode._validate("")


def test_rejects_leading_underscore() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        GenreCode._validate("_foo")


def test_rejects_trailing_underscore() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        GenreCode._validate("foo_")


def test_rejects_uppercase() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        GenreCode._validate("FooBar")


def test_rejects_spaces() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        GenreCode._validate("foo bar")


def test_pydantic_field_integration() -> None:
    """GenreCode works as a pydantic field type."""

    class M(BaseModel):
        code: GenreCode

    m = M(code="mutant_wasteland")  # type: ignore[arg-type]
    assert str(m.code) == "mutant_wasteland"


def test_pydantic_rejects_invalid() -> None:
    class M(BaseModel):
        code: GenreCode

    with pytest.raises(Exception):
        M(code="Bad Code")  # type: ignore[arg-type]
