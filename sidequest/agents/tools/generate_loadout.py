"""Tool: generate_loadout — equipment loadout for archetype + tier.

Phase C Task 25 — GENERATE tool
-------------------------------
The Rust prototype shipped a ``loadoutgen`` CLI that produced
archetype/tier-keyed starting equipment. The Python port (ADR-082)
hasn't ported that subsystem yet — ``sidequest.cli.loadoutgen`` is a
placeholder module containing only the marker comment::

    # Placeholder — populated in later phases per ADR-082 port plan.

Per the Phase C plan we **still register the tool**:

1. Reserves the namespace so the narrator's tool-use spec is stable
   across phases.
2. Lets the narrator's intent reach the GM panel — every loadout
   request is recorded with full OTEL attrs (``archetype``, ``tier``,
   ``genre``) and an unwired marker, so Keith can see what the
   narrator wanted even though the subsystem hasn't landed.

The tool returns a successful :class:`ToolResult` with an empty
``items`` list and ``loadoutgen_wired=False``. A future contributor
who lands the port flips that flag and populates ``items``; nothing
else in the registry/tool surface needs to change. Phase D/E will
revisit this once :mod:`sidequest.cli.loadoutgen` is populated.

OTEL attributes
~~~~~~~~~~~~~~~
* ``tool.loadout.archetype`` — archetype slug requested.
* ``tool.loadout.tier`` — tier requested (1-5).
* ``tool.loadout.genre`` — genre slug or empty string when omitted.
* ``tool.loadout.item_count`` — always ``0`` while the subsystem is
  unwired.
* ``tool.loadout.loadoutgen_wired`` — bool; ``False`` until the
  ``loadoutgen`` subsystem is ported.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)


class GenerateLoadoutArgs(BaseModel):
    model_config = {"extra": "forbid"}

    archetype: str = Field(
        ...,
        min_length=1,
        description=(
            "Character archetype slug (e.g. 'fighter', 'rogue', 'scout'). "
            "Narrator-facing; will be matched against genre-pack-defined "
            "archetypes when the loadoutgen subsystem is wired."
        ),
    )
    tier: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "Loadout tier (1=starting kit, 5=elite). Bounded 1..5 to match "
            "the encountergen tier ladder."
        ),
    )
    genre: str | None = Field(
        default=None,
        description=(
            "Optional genre slug override. When omitted the production wire "
            "site will use the active session's genre pack."
        ),
    )


@tool(
    name="generate_loadout",
    description="Generate an equipment loadout for an archetype + tier.",
    category=ToolCategory.GENERATE,
)
async def generate_loadout(args: GenerateLoadoutArgs, ctx: ToolContext) -> ToolResult:
    # sidequest.cli.loadoutgen is a placeholder per ADR-082 — the Python
    # port hasn't ported the Rust prototype's loadoutgen CLI. v1 records
    # the request and returns an empty loadout. Phase D/E may implement.
    ctx.otel_span.set_attribute("tool.loadout.archetype", args.archetype)
    ctx.otel_span.set_attribute("tool.loadout.tier", args.tier)
    ctx.otel_span.set_attribute("tool.loadout.genre", args.genre or "")
    ctx.otel_span.set_attribute("tool.loadout.item_count", 0)
    ctx.otel_span.set_attribute("tool.loadout.loadoutgen_wired", False)
    return ToolResult.ok(
        {
            "archetype": args.archetype,
            "tier": args.tier,
            "genre": args.genre,
            "items": [],
            "loadoutgen_wired": False,
            "note": (
                "sidequest.cli.loadoutgen is a placeholder per ADR-082 — Python port pending."
            ),
        }
    )
