"""Tests for GenreError exception hierarchy.

Port of the error types from sidequest-genre/src/error.rs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.genre.error import (
    GenreCycleError,
    GenreError,
    GenreIoError,
    GenreLoadError,
    GenreMissingParentError,
    GenreNotFoundError,
    GenreValidationError,
    SchemaValidationError,
    ValidationErrors,
)

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_all_errors_are_genre_error_subclasses() -> None:
    """All concrete error classes inherit from GenreError."""
    assert issubclass(GenreLoadError, GenreError)
    assert issubclass(GenreCycleError, GenreError)
    assert issubclass(GenreMissingParentError, GenreError)
    assert issubclass(GenreValidationError, GenreError)
    assert issubclass(GenreIoError, GenreError)
    assert issubclass(GenreNotFoundError, GenreError)
    assert issubclass(SchemaValidationError, GenreError)


# ---------------------------------------------------------------------------
# GenreLoadError
# ---------------------------------------------------------------------------


def test_genre_load_error_message_format() -> None:
    """Port of Rust #[error("failed to load {path}: {detail}")]."""
    err = GenreLoadError(path=Path("/pack/archetype.yaml"), detail="file not found")
    assert "failed to load" in str(err)
    assert "archetype.yaml" in str(err)
    assert "file not found" in str(err)


def test_genre_load_error_accepts_string_path() -> None:
    err = GenreLoadError(path="some/path.yaml", detail="bad")
    assert isinstance(err.path, Path)


# ---------------------------------------------------------------------------
# GenreCycleError
# ---------------------------------------------------------------------------


def test_genre_cycle_error_message_format() -> None:
    """Port of Rust #[error("cycle detected in trope inheritance: {trope}")]."""
    err = GenreCycleError(trope="mentor")
    assert "cycle detected in trope inheritance" in str(err)
    assert "mentor" in str(err)
    assert err.trope == "mentor"


# ---------------------------------------------------------------------------
# GenreMissingParentError
# ---------------------------------------------------------------------------


def test_genre_missing_parent_error_message_format() -> None:
    """Port of Rust #[error("trope '{trope}' extends '{parent}' which does not exist")]."""
    err = GenreMissingParentError(trope="child_trope", parent="ghost_parent")
    assert "child_trope" in str(err)
    assert "ghost_parent" in str(err)
    assert "does not exist" in str(err)
    assert err.trope == "child_trope"
    assert err.parent == "ghost_parent"


# ---------------------------------------------------------------------------
# GenreValidationError
# ---------------------------------------------------------------------------


def test_genre_validation_error_message_format() -> None:
    """Port of Rust #[error("validation error: {message}")]."""
    err = GenreValidationError(message="missing required field")
    assert "validation error" in str(err)
    assert "missing required field" in str(err)
    assert err.message == "missing required field"


# ---------------------------------------------------------------------------
# GenreIoError
# ---------------------------------------------------------------------------


def test_genre_io_error_message_format() -> None:
    """Port of Rust #[error("I/O error: {message}")]."""
    err = GenreIoError(message="permission denied")
    assert "I/O error" in str(err)
    assert "permission denied" in str(err)
    assert err.message == "permission denied"


# ---------------------------------------------------------------------------
# GenreNotFoundError
# ---------------------------------------------------------------------------


def test_genre_not_found_error_message_format() -> None:
    """Port of Rust #[error("genre pack '{code}' not found; searched: {}", ...)]."""
    err = GenreNotFoundError(code="atlantis", searched=["/packs/a", "/packs/b"])
    assert "atlantis" in str(err)
    assert "/packs/a" in str(err)
    assert "/packs/b" in str(err)
    assert err.code == "atlantis"
    assert err.searched == ["/packs/a", "/packs/b"]


# ---------------------------------------------------------------------------
# SchemaValidationError
# ---------------------------------------------------------------------------


def test_schema_validation_error_message_format() -> None:
    err = SchemaValidationError(message="field 'name' is required")
    assert "schema validation error" in str(err)
    assert "field 'name' is required" in str(err)
    assert err.message == "field 'name' is required"


# ---------------------------------------------------------------------------
# ValidationErrors aggregator
# ---------------------------------------------------------------------------


def test_validation_errors_starts_empty() -> None:
    """Port of Rust ValidationErrors::new() / is_empty()."""
    errs = ValidationErrors()
    assert errs.is_empty()
    assert len(errs) == 0


def test_validation_errors_push_increments_len() -> None:
    """Port of Rust ValidationErrors::push() / len()."""
    errs = ValidationErrors()
    errs.push(GenreValidationError("bad field"))
    errs.push(GenreValidationError("another bad field"))
    assert len(errs) == 2
    assert not errs.is_empty()


def test_validation_errors_into_result_raises_when_non_empty() -> None:
    """Port of Rust ValidationErrors::into_result() -> Err(self)."""
    errs = ValidationErrors()
    errs.push(GenreValidationError("oops"))
    with pytest.raises(ValidationErrors):
        errs.into_result()


def test_validation_errors_into_result_passes_when_empty() -> None:
    """Port of Rust ValidationErrors::into_result() -> Ok(())."""
    errs = ValidationErrors()
    errs.into_result()  # must not raise


def test_validation_errors_str_includes_count_and_errors() -> None:
    """Port of Rust ValidationErrors Display impl."""
    errs = ValidationErrors()
    errs.push(GenreValidationError("first"))
    errs.push(GenreCycleError("loop_trope"))
    s = str(errs)
    assert "2 validation error(s)" in s
    assert "first" in s
    assert "loop_trope" in s


def test_validation_errors_errors_property_returns_copy() -> None:
    """errors property returns a copy — mutating it doesn't affect the aggregator."""
    errs = ValidationErrors()
    errs.push(GenreValidationError("x"))
    copy = errs.errors
    copy.clear()
    assert len(errs) == 1


def test_validation_errors_is_catchable_as_exception() -> None:
    """ValidationErrors is itself an Exception and can be caught."""
    errs = ValidationErrors()
    errs.push(GenreIoError("disk gone"))
    with pytest.raises(Exception):  # noqa: B017 — intentional: testing Exception-catchability contract
        errs.into_result()
