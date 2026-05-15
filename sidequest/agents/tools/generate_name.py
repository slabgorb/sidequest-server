"""Tool: generate_name — culture-corpus + Markov chain name generation.

Phase C Task 24 — GENERATE tool
-------------------------------
ADR-091 (culture-corpus Markov naming) defines per-culture corpora —
plain-text training files in ``sidequest-content/genre_packs/<pack>/corpus/``
— combined with template patterns from ``cultures.yaml`` to produce
genre-true names without burning a Claude turn on it. The narrator calls
this tool when it needs one or more fresh names for NPCs, places,
taverns, or ships in the current scene.

The engine entry point is
:func:`sidequest.genre.names.generator.build_from_culture`, which
materialises a :class:`NameGenerator` from a :class:`Culture` model and
a corpus directory ``Path``. The :class:`NameGenerator` carries:

- ``slots: dict[str, SlotGenerator]`` — keyed by slot name
  (``given_name``, ``surname``, etc.). Each generator's ``.generate()``
  emits a single Markov-chained token.
- ``person_patterns`` / ``place_patterns`` — list of ``str.format``
  templates assembled from slots via
  ``NameGenerator.generate_person()`` / ``.generate_place()``.

Phase B amendment #4
~~~~~~~~~~~~~~~~~~~~
:class:`~sidequest.agents.tool_registry.ToolContext` gains an optional
``name_generators: dict[str, NameGenerator] | None``. Phase E wires the
production call site (constructing the dict at session-load by walking
``genre_pack.cultures`` and calling ``build_from_culture`` per culture
with the genre pack's corpus directory). Phase C tools tolerate
``None`` — the tool returns an empty ``names`` list with
``name_generators_wired=False`` and stamps an OTEL marker so the GM
panel can see the unwired call.

Kind → slot mapping
~~~~~~~~~~~~~~~~~~~
The ``kind`` enum is a stable narrator-facing surface; actual slot names
in ``cultures.yaml`` vary by culture (Surface Folk has ``given_name``
and ``surname``; Keeper Titles has ``noun``/``abstract``/``adjective``).
We resolve the requested ``kind`` against the culture's actual slots:

* ``given``  → slot ``given_name``
* ``family`` → slot ``surname``, then ``family_name`` (cultures use one
  or the other)
* ``place``  → ``NameGenerator.generate_place()`` (template-driven, uses
  ``place_patterns``)
* ``tavern`` → slot ``tavern_name``
* ``ship``   → slot ``ship_name``

If the requested slot/pattern set is missing on the culture, the tool
returns ``not_found`` with a helpful message listing the slots that
*are* available. The narrator can then re-call with a supported ``kind``
or pick another culture.

OTEL attributes
~~~~~~~~~~~~~~~
* ``tool.namegen.culture`` — culture name requested.
* ``tool.namegen.kind`` — kind requested.
* ``tool.namegen.count`` — count of names actually returned (0 on
  unwired/unknown paths).
* ``tool.namegen.name_generators_wired`` — bool; ``False`` until Phase E.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)

# Narrator-facing kind enum. Stable across cultures.
_NameKind = Literal["given", "family", "place", "tavern", "ship"]

# Kind → ordered list of culture slot names to try. The first slot that
# exists on the culture wins. ``place`` is special-cased to use
# generate_place() via place_patterns and is omitted from this map.
_KIND_TO_SLOTS: dict[str, tuple[str, ...]] = {
    "given": ("given_name",),
    "family": ("surname", "family_name"),
    "tavern": ("tavern_name",),
    "ship": ("ship_name",),
}


class GenerateNameArgs(BaseModel):
    model_config = {"extra": "forbid"}

    culture: str = Field(
        ...,
        min_length=1,
        description=(
            "Culture name. Must match a culture defined in the genre pack "
            "(e.g. 'Surface Folk', 'Keeper Titles'). Case-sensitive — matches "
            "the Culture.name field from cultures.yaml."
        ),
    )
    kind: _NameKind = Field(
        default="given",
        description=(
            "What kind of name to generate. Maps to culture slots: 'given'→"
            "given_name, 'family'→surname/family_name, 'place'→place_patterns, "
            "'tavern'→tavern_name, 'ship'→ship_name. Returns not_found if the "
            "culture has no matching slot or pattern."
        ),
    )
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of names to generate (capped at 10 per call).",
    )


@tool(
    name="generate_name",
    description=(
        "Generate one or more names from the named culture's corpus via the "
        "Markov chain. Cultures are genre-pack-defined."
    ),
    category=ToolCategory.GENERATE,
)
async def generate_name(args: GenerateNameArgs, ctx: ToolContext) -> ToolResult:
    if ctx.name_generators is None:
        # Phase C: no production wire yet. OTEL-stamp the unwired marker
        # and return an empty list so the narrator can see the tool fired
        # without raising.
        ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
        ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
        ctx.otel_span.set_attribute("tool.namegen.count", 0)
        ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", False)
        return ToolResult.ok(
            {
                "culture": args.culture,
                "kind": args.kind,
                "names": [],
                "name_generators_wired": False,
            }
        )

    namegen = ctx.name_generators.get(args.culture)
    if namegen is None:
        available = sorted(ctx.name_generators.keys())
        ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
        ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
        ctx.otel_span.set_attribute("tool.namegen.count", 0)
        ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", True)
        return ToolResult.not_found(f"unknown culture {args.culture!r}; available: {available}")

    names: list[str] = []
    try:
        if args.kind == "place":
            # Template-driven path: place_patterns are required.
            if not namegen.place_patterns:
                ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
                ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
                ctx.otel_span.set_attribute("tool.namegen.count", 0)
                ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", True)
                return ToolResult.not_found(
                    f"culture {args.culture!r} has no place_patterns; cannot "
                    f"generate kind='place'. Available slots: "
                    f"{sorted(namegen.slots.keys())}"
                )
            for _ in range(args.count):
                names.append(namegen.generate_place())
        else:
            # Slot-driven path: resolve kind → first matching slot present
            # on the culture.
            slot_candidates = _KIND_TO_SLOTS[args.kind]
            resolved_slot: str | None = next(
                (s for s in slot_candidates if s in namegen.slots),
                None,
            )
            if resolved_slot is None:
                available_slots = sorted(namegen.slots.keys())
                ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
                ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
                ctx.otel_span.set_attribute("tool.namegen.count", 0)
                ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", True)
                return ToolResult.not_found(
                    f"culture {args.culture!r} has no slot for kind={args.kind!r} "
                    f"(tried {list(slot_candidates)}); available slots: "
                    f"{available_slots}"
                )
            slot_gen = namegen.slots[resolved_slot]
            for _ in range(args.count):
                names.append(slot_gen.generate())
    except Exception as exc:
        # Any underlying generator failure (e.g. empty corpus + empty
        # word_list) is reported as a recoverable error so the narrator
        # can try another kind/culture.
        ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
        ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
        ctx.otel_span.set_attribute("tool.namegen.count", 0)
        ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", True)
        return ToolResult.error(
            f"generator failed for culture={args.culture!r} kind={args.kind!r}: "
            f"{type(exc).__name__}: {exc}"
        )

    ctx.otel_span.set_attribute("tool.namegen.culture", args.culture)
    ctx.otel_span.set_attribute("tool.namegen.kind", args.kind)
    ctx.otel_span.set_attribute("tool.namegen.count", len(names))
    ctx.otel_span.set_attribute("tool.namegen.name_generators_wired", True)

    return ToolResult.ok(
        {
            "culture": args.culture,
            "kind": args.kind,
            "names": names,
            "name_generators_wired": True,
        }
    )
