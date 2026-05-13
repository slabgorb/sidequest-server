"""Disposition → attitude band mapping (ADR-020 three-tier).

Story 50-10 restored the qualitative attitude layer that was dropped
during the 2026-04 Python port. The Rust-era pattern lived as an
``Attitude`` enum + ``Disposition(i32)`` newtype with ``.attitude()``
derivation; this module mirrors that shape in Python.

The numeric layer (``Disposition.value``) and the qualitative layer
(``Disposition.attitude()`` → ``Attitude``) are deliberately kept
separate. The world-state agent reasons in numbers ("+5 disposition");
the narrator and NPC-presentation agents reason in attitudes
("the bartender is friendly"). Keeping the two layers explicit lets
each agent see only what it needs.

The string values ``"friendly"`` / ``"neutral"`` / ``"hostile"`` are the
stable wire contract — OTEL spans (``SPAN_DISPOSITION_SHIFT``), the GM
panel, the scrapbook, and the narrator's NPC serialization all match on
those exact literals.

Boundaries (strict, per ADR-020):

- ``value > 10`` → friendly
- ``value < -10`` → hostile
- otherwise → neutral

10 is neutral, 11 is friendly. -10 is neutral, -11 is hostile. The
``Disposition`` constructor clamps any integer to ``-100..+100``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

__all__ = ["Attitude", "Disposition"]


class Attitude(StrEnum):
    """Three-tier attitude band per ADR-020.

    ``StrEnum`` so the enum members compare equal to their string values
    and serialize naturally into OTEL span attributes and JSON. Downstream
    consumers (the GM panel, the narrator) match on the literal strings;
    keeping ``Attitude`` a ``str`` subclass means a ``before_attitude``
    field carrying ``Attitude.FRIENDLY`` reads back from the watcher's
    JSON pipe as ``"friendly"`` without any custom serializer.
    """

    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    HOSTILE = "hostile"


class Disposition:
    """NPC disposition score with attitude derivation.

    Wraps a clamped integer in ``-100..+100`` and exposes a single
    derivation method, ``attitude()``, that returns the qualitative band.
    Treated as a value type — distinct ``Disposition`` instances per NPC,
    no shared mutable state across the snapshot.

    Construction accepts a raw int, defaulting to 0. Pydantic models
    that reference ``Disposition`` as a field type get coercion from
    raw int via ``__get_pydantic_core_schema__`` so existing fixtures
    (``Npc(disposition=15)``) continue to work without rewriting every
    integration test.
    """

    __slots__ = ("value",)

    def __init__(self, value: int = 0) -> None:
        self.value = max(-100, min(100, int(value)))

    def attitude(self) -> Attitude:
        if self.value > 10:
            return Attitude.FRIENDLY
        if self.value < -10:
            return Attitude.HOSTILE
        return Attitude.NEUTRAL

    def __int__(self) -> int:
        return self.value

    def __repr__(self) -> str:
        return f"Disposition({self.value})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        """Pydantic v2 schema hook.

        Accepts a ``Disposition`` instance (pass-through) or a raw int
        (coerced via ``Disposition(int)`` with clamping). Serializes back
        to a bare ``int`` so save-file JSON stays human-readable and the
        GM panel can read the numeric value without unpacking a wrapper.
        """

        def _from_int(v: int) -> Disposition:
            return cls(v)

        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(cls),
                core_schema.no_info_after_validator_function(
                    _from_int,
                    core_schema.int_schema(),
                ),
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda d: d.value,
                return_schema=core_schema.int_schema(),
            ),
        )
