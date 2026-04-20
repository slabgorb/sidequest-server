"""Validated newtypes for the protocol layer.

Port of sidequest-protocol/src/types.rs.

NonBlankString and Stat are RootModel[str] wrappers with validation.
Both serialize/deserialize as plain JSON strings (transparent semantics).
"""

from __future__ import annotations

from pydantic import RootModel, model_validator


class NonBlankString(RootModel[str]):
    """A string guaranteed to be non-empty after trimming.

    Serializes as a plain JSON string (transparent). Rejects blank/whitespace.
    The stored value is trimmed.
    """

    @model_validator(mode="before")
    @classmethod
    def _validate(cls, v: object) -> object:
        if isinstance(v, str):
            trimmed = v.strip()
            if not trimmed:
                raise ValueError("string must not be blank")
            return trimmed
        return v

    def as_str(self) -> str:
        return self.root

    def __str__(self) -> str:
        return self.root

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NonBlankString):
            return self.root == other.root
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.root)


class Stat(RootModel[str]):
    """A canonicalized ability/stat name.

    Normalized to UPPERCASE at construction. Rejects blank/whitespace.
    Serializes as a plain JSON string (transparent).
    """

    @model_validator(mode="before")
    @classmethod
    def _validate(cls, v: object) -> object:
        if isinstance(v, str):
            trimmed = v.strip()
            if not trimmed:
                raise ValueError("stat name must not be blank")
            return trimmed.upper()
        return v

    def as_str(self) -> str:
        return self.root

    def __str__(self) -> str:
        return self.root

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Stat):
            return self.root == other.root
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.root)
