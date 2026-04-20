"""Base class for sidequest.protocol pydantic models.

Rust uses #[serde(skip_serializing_if = "...")] on many payload fields to omit
empty/None values from the wire. pydantic v2 doesn't do this by default.
This base class flips the default so serialization matches Rust:
  - None fields are omitted (Rust Option::is_none)
  - Empty lists, dicts, and strings are omitted when the field's declared
    default is also empty (Rust Vec::is_empty / String::is_empty)

Implemented via @model_serializer(mode='wrap') so the behavior applies
in both direct serialization AND when models are nested inside a RootModel
(e.g., GameMessage wrapping a payload).

Numeric fields (int, float, bool) with zero/false defaults are NOT dropped —
Rust doesn't use skip_serializing_if on those types.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_serializer
from pydantic_core.core_schema import SerializerFunctionWrapHandler


class ProtocolBase(BaseModel):
    """Base class for all sidequest protocol models.

    @model_serializer applies Rust-equivalent skip_serializing_if semantics:
      - None → omitted (Rust Option::is_none)
      - empty list/dict/str matching its declared default → omitted (Rust is_empty)
      - numeric/bool fields → always present regardless of value

    Works in nested contexts (e.g., inside GameMessage RootModel).

    Fields can opt out by setting a non-empty default value (they'll always
    be included then), or by passing explicit override args to model_dump().
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_serializer(mode="wrap")
    def _protocol_serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize omitting None and empty containers that match their defaults."""
        d = handler(self)
        cls = type(self)
        result: dict[str, Any] = {}
        for k, v in d.items():
            # Always drop None (covers all Option<T> fields)
            if v is None:
                continue
            # Drop empty list/dict/str ONLY when the field default is also empty.
            # This mirrors Rust's skip_serializing_if = "Vec::is_empty" and
            # skip_serializing_if = "String::is_empty" — numeric/bool fields
            # are never dropped here.
            if isinstance(v, (list, dict, str)) and not v:
                field_info = _find_field(cls, k)
                if field_info is not None:
                    default_val = _field_default(field_info)
                    if default_val == v:
                        continue
            result[k] = v
        return result

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """model_dump respects the same exclude logic as serialization.

        Callers may override exclude_none / exclude_defaults explicitly.
        """
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs: Any) -> str:
        """model_dump_json is handled by @model_serializer — this override
        exists only for call sites that pass explicit kwargs like by_alias.
        The serializer already excludes None/empty; no additional work needed.
        """
        return super().model_dump_json(**kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_field(cls: type, serialized_key: str) -> Any | None:
    """Return the FieldInfo for the field that serializes to `serialized_key`."""
    for fname, finfo in cls.model_fields.items():
        if fname == serialized_key:
            return finfo
        if getattr(finfo, "alias", None) == serialized_key:
            return finfo
        if getattr(finfo, "serialization_alias", None) == serialized_key:
            return finfo
    return None


def _field_default(field_info: Any) -> Any:
    """Return the declared default value for a FieldInfo."""
    default_factory = getattr(field_info, "default_factory", None)
    if default_factory is not None:
        try:
            return default_factory()
        except Exception:
            return None
    default = getattr(field_info, "default", ...)
    if default is ... or default is None:
        return None
    return default
