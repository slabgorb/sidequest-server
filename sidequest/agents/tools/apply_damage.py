"""Tool: apply_damage — narrator-driven HP damage → engine edge delta.

Translation (ADR-078):
    The narrator's mental model still speaks "HP damage" (the universal
    tabletop verb). The engine model is *edge / composure* — there is no
    HP field. This adapter performs the translation at the boundary:

        narrator: apply_damage(target=Alice, amount=4, ...)
                        |
                        v
        engine:   CreatureCore.apply_edge_delta(-4)

    The OTEL attribute name is ``tool.damage.target_edge_after`` (and the
    payload field is ``target_edge_after``) on purpose — propagating the
    misleading "hp" name into new code would muddle the ADR-078 model
    every time a future reader touches it. The tool-name surface
    (``apply_damage``) is the only place the legacy verb survives, because
    that's the word the narrator actually uses.

The OTEL span is emitted via the Phase B Registry dispatcher
(``tool.write.apply_damage``); this handler enriches it with the
per-tool ``tool.damage.*`` attributes the GM panel reads.

Sequential-per-session execution is provided by the Registry's
``_write_locks`` map — WRITE handlers don't need their own locking.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)


class ApplyDamageArgs(BaseModel):
    target: str = Field(..., description="Name of the character or NPC to damage.")
    amount: int = Field(
        ...,
        ge=0,
        description="Damage amount. 0 is valid (no-op) and still emits the span.",
    )
    damage_type: str = Field(
        default="untyped",
        description="Genre-flavored damage type (slashing/fire/psychic/etc.).",
    )
    source: str = Field(
        default="",
        description="One-line cause description; surfaces in OTEL for GM-panel review.",
    )


@tool(
    name="apply_damage",
    description=(
        "Apply HP damage to a character or combatant. Use after a roll has "
        "determined the damage amount. `damage_type` is genre-flavored "
        "(slashing/fire/psychic/etc.); `source` is a one-line cause "
        "description."
    ),
    category=ToolCategory.WRITE,
)
async def apply_damage(args: ApplyDamageArgs, ctx: ToolContext) -> ToolResult:
    session = ctx.store.load()
    if session is None:
        return ToolResult.error("no active session", recoverable=False)

    snapshot = session.snapshot
    core = snapshot.find_creature_core(args.target)
    if core is None:
        return ToolResult.not_found(f"unknown target: {args.target!r}")

    # Translate "damage amount" → negative edge delta. amount=0 is a
    # deliberate no-op but we still walk the persistence path so the
    # span lands and any narrator audit trail stays consistent.
    core.apply_edge_delta(-args.amount)
    target_edge_after = core.edge.current

    ctx.store.save(snapshot)

    ctx.otel_span.set_attribute("tool.damage.target", args.target)
    ctx.otel_span.set_attribute("tool.damage.amount", args.amount)
    ctx.otel_span.set_attribute("tool.damage.damage_type", args.damage_type)
    ctx.otel_span.set_attribute("tool.damage.source", args.source)
    ctx.otel_span.set_attribute("tool.damage.target_edge_after", target_edge_after)

    return ToolResult.ok(
        {
            "target": args.target,
            "amount": args.amount,
            "damage_type": args.damage_type,
            "source": args.source,
            "target_edge_after": target_edge_after,
        }
    )
