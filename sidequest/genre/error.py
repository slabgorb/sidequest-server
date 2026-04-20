"""Error types for the genre pack loader.

Port of sidequest-genre/src/error.rs — GenreError enum + ValidationErrors aggregator.

Python exception hierarchy mirrors the Rust enum variants. Each subclass carries
the same fields as the Rust variant struct.
"""

from __future__ import annotations

from pathlib import Path


class GenreError(Exception):
    """Base class for sidequest.genre errors.

    Port of Rust GenreError enum. All genre-pack loading, resolution, and
    validation errors descend from this class.
    """


class GenreLoadError(GenreError):
    """A YAML file could not be read or parsed.

    Port of Rust GenreError::LoadError { path, detail }.
    """

    def __init__(self, path: Path | str, detail: str) -> None:
        self.path = Path(path)
        self.detail = detail
        super().__init__(f"failed to load {self.path}: {self.detail}")


class GenreCycleError(GenreError):
    """A trope `extends` chain contains a cycle.

    Port of Rust GenreError::CycleDetected { trope }.
    """

    def __init__(self, trope: str) -> None:
        self.trope = trope
        super().__init__(f"cycle detected in trope inheritance: {trope}")


class GenreMissingParentError(GenreError):
    """A trope references a parent via `extends` that does not exist.

    Port of Rust GenreError::MissingParent { trope, parent }.
    """

    def __init__(self, trope: str, parent: str) -> None:
        self.trope = trope
        self.parent = parent
        super().__init__(f"trope '{trope}' extends '{parent}' which does not exist")


class GenreValidationError(GenreError):
    """Cross-reference validation failed.

    Port of Rust GenreError::ValidationError { message }.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"validation error: {message}")


class GenreIoError(GenreError):
    """An I/O error occurred while reading a tier file.

    Port of Rust GenreError::IoError { message }.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"I/O error: {message}")


class GenreNotFoundError(GenreError):
    """Genre pack not found in any search path.

    Port of Rust GenreError::NotFound { code, searched }.
    """

    def __init__(self, code: str, searched: list[str]) -> None:
        self.code = code
        self.searched = searched
        searched_str = ", ".join(searched)
        super().__init__(f"genre pack '{code}' not found; searched: {searched_str}")


class SchemaValidationError(GenreError):
    """YAML content failed pydantic schema validation.

    No direct Rust equivalent — this covers the serde_yaml parse errors
    that Rust surfaces as GenreError::ValidationError in practice.
    Kept distinct from GenreValidationError (cross-ref validation) to
    preserve the semantic difference between "bad file content" and
    "inconsistent cross-pack references".
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"schema validation error: {message}")


class ValidationErrors(Exception):
    """A collection of validation errors, supporting error aggregation.

    Port of Rust ValidationErrors struct. Instead of failing on the first
    error, validation collects all errors and reports them together.

    Usage:
        errs = ValidationErrors()
        errs.push(GenreValidationError("bad field"))
        errs.into_result()  # raises self if non-empty
    """

    def __init__(self) -> None:
        self._errors: list[GenreError] = []

    def push(self, error: GenreError) -> None:
        """Add a validation error to the collection."""
        self._errors.append(error)

    def __len__(self) -> int:
        return len(self._errors)

    def is_empty(self) -> bool:
        """Returns True if no errors have been collected."""
        return len(self._errors) == 0

    @property
    def errors(self) -> list[GenreError]:
        """The collected errors (read-only view)."""
        return list(self._errors)

    def into_result(self) -> None:
        """Raise self if any errors were collected, otherwise return None.

        Port of Rust ValidationErrors::into_result() -> Result<(), Self>.
        """
        if self._errors:
            raise self

    def __str__(self) -> str:
        lines = [f"{len(self._errors)} validation error(s):"]
        for i, err in enumerate(self._errors, start=1):
            lines.append(f"  {i}: {err}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ValidationErrors({self._errors!r})"
