"""GenreCode — validated newtype for genre pack codes.

Port of sidequest-genre/src/genre_code.rs.

Valid codes: lowercase alphanumeric with underscores, no leading/trailing underscores.
Examples: "mutant_wasteland", "low_fantasy", "caverns_and_claudes".
"""

from __future__ import annotations

import re

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

_GENRE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]*[a-z0-9]$|^[a-z0-9]$")


class GenreCode(str):
    """Validated genre pack code (snake_case identifier).

    Accepts: lowercase alpha-numeric with underscores.
    Rejects: empty, leading/trailing underscores, uppercase, spaces.
    """

    @classmethod
    def _validate(cls, value: str) -> "GenreCode":
        if not value:
            raise ValueError("genre code must not be empty")
        if value.startswith("_") or value.endswith("_"):
            raise ValueError(
                f"invalid genre code format: '{value}' (must be lowercase snake_case)"
            )
        for ch in value:
            if not (ch.islower() or ch.isdigit() or ch == "_"):
                raise ValueError(
                    f"invalid genre code format: '{value}' (must be lowercase snake_case)"
                )
        return cls(value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: type, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_]*[a-z0-9]$|^[a-z0-9]$"}
