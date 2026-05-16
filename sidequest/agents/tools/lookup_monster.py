"""Tool: lookup_monster — Monster Manual entry fetch (lore-safe surface).

Phase C Task 14 — read tool, no perception rule
-----------------------------------------------
ADR-059 (Monster Manual — Server-Side Pre-Generation via Game-State
Injection) introduced a persistent per-genre/world pool of
:class:`~sidequest.game.monster_manual.MonsterManual` entries (NPCs +
encounter seeds). Pre-game tools (namegen, encountergen) populate the
Manual; at narration time the Manual is *injected* into the
``<game_state>`` block so the narrator uses pool names naturally. The
post-narration compound-key lookup path also reads from the same Manual.

This tool exposes a deliberate, narrow read path: given a creature/NPC
display name, return the lore-safe descriptive surface (role, culture,
location tags, lifecycle state) so the narrator can ground its prose
without pulling the full stat data into the prompt.

Phase B amendment
~~~~~~~~~~~~~~~~~
:class:`~sidequest.agents.tool_registry.ToolContext` was extended with an
optional ``monster_manual: MonsterManual | None`` field for this tool,
paralleling the ``lore_store`` amendment from Task 13. The
:class:`MonsterManual` is per-genre/world and lives on
:class:`~sidequest.server.session_handler.SessionHandler` (loaded via
:meth:`MonsterManual.load`), not on the
:class:`~sidequest.game.persistence.SqliteStore` save layer, so it is not
reachable via ``ctx.store``. Production wiring (constructing the ctx
with the session-handler's MonsterManual) is Phase E.

When ``ctx.monster_manual is None`` (e.g. during tests, or before Phase
E lands), the tool returns ``found=False`` and stamps the dispatch span
with ``tool.monster.monster_manual_wired = False`` so the GM panel can
detect un-wired calls. This is intentionally NOT a hard error — Phase E
will flip it on without revisiting Phase C tools.

v1 hard-gate on ``include_stat_block``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The plan envisioned a perception-gated stat block: ``include_stat_block``
would return full mechanical data only when the PC's recognize-check had
succeeded or the GM had flagged the monster as known. In v1 we **hard-
gate**: the arg is accepted forward-compat (so the SDK schema doesn't
churn later), but stat data is NEVER returned, regardless of the arg.

The narrator gets back ``stat_block_included=False`` and, when it asked
for the data, a ``stat_block_gate_reason`` string so it knows the
request was accepted but the data deliberately withheld. When a per-PC
recognize-check system lands (post-Phase D), the gate branch below will
check ``ctx.perspective_pc`` against a per-PC known-monsters set and
conditionally include the underlying ``entry.data`` dict.

Perception rule
~~~~~~~~~~~~~~~
None registered. The v1 in-handler hard-gate is strictly stricter than
any per-PC perception rule could be; registering an additional
perception rule would be redundant. When the recognize-check system
lands and the in-handler gate softens, a perception rule may be added.

Payload
~~~~~~~
On hit::

    {
      "name": <display name>,
      "role": <role>,
      "culture": <culture>,
      "location_tags": [<tag>, ...],
      "state": <lifecycle state>,
      "activated_location": <str | None>,
      "monster_manual_wired": True,
      "stat_block_included": False,
      # only when include_stat_block was requested:
      "stat_block_gate_reason": <str>,
    }

On un-wired ctx::

    {"name": <arg>, "found": False, "monster_manual_wired": False}

OTEL
~~~~
* ``tool.monster.name`` — the requested name.
* ``tool.monster.stat_block_included`` — always ``False`` in v1.
* ``tool.monster.monster_manual_wired`` — bool. ``False`` when Phase E
  has not yet wired the production ctx; ``True`` otherwise.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)

_STAT_BLOCK_GATE_REASON = "v1 hard-gates stat blocks pending per-PC recognize-check system"


class LookupMonsterArgs(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Monster/NPC display name. Matches MonsterManual.find_npc_by_name "
            "(case-insensitive, fuzzy substring)."
        ),
    )
    include_stat_block: bool = Field(
        default=False,
        description=(
            "When True, requests the full pre-generated stat data dict. v1 "
            "HARD-GATES this — accepted forward-compat but data is never "
            "returned until a per-PC recognize-check system lands. The "
            "response will indicate stat_block_included=False with a "
            "stat_block_gate_reason string."
        ),
    )


@tool(
    name="lookup_monster",
    description=(
        "Fetch a monster manual entry by name. Default returns the lore-safe "
        "surface (description, behavior cues); request include_stat_block=true "
        "only when actually resolving mechanics."
    ),
    category=ToolCategory.READ,
)
async def lookup_monster(args: LookupMonsterArgs, ctx: ToolContext) -> ToolResult:
    if ctx.monster_manual is None:
        # Phase E wires the MonsterManual into ToolContext at the production
        # call site. Until then, return found=False with an OTEL marker so
        # the GM panel can see the tool fired but the manual wasn't reachable.
        ctx.otel_span.set_attribute("tool.monster.name", args.name)
        ctx.otel_span.set_attribute("tool.monster.stat_block_included", False)
        ctx.otel_span.set_attribute("tool.monster.monster_manual_wired", False)
        return ToolResult.ok(
            {
                "name": args.name,
                "found": False,
                "monster_manual_wired": False,
            }
        )

    entry = ctx.monster_manual.find_npc_by_name(args.name)
    if entry is None:
        ctx.otel_span.set_attribute("tool.monster.name", args.name)
        ctx.otel_span.set_attribute("tool.monster.stat_block_included", False)
        ctx.otel_span.set_attribute("tool.monster.monster_manual_wired", True)
        return ToolResult.not_found(f"no monster named {args.name!r} in manual")

    # Lore-safe surface — never the raw entry.data dict.
    payload: dict[str, object] = {
        "name": entry.name,
        "role": entry.role,
        "culture": entry.culture,
        "location_tags": list(entry.location_tags),
        "state": entry.state.value,
        "activated_location": entry.activated_location,
        "monster_manual_wired": True,
        "stat_block_included": False,
    }

    if args.include_stat_block:
        # v1 hard-gate. The arg is accepted (forward-compat for the SDK
        # schema) but the stat data is NOT returned. The narrator sees the
        # gate reason so it knows the request was deliberately withheld.
        # Future: check ctx.perspective_pc against a per-PC known-monsters
        # set and conditionally include entry.data here.
        payload["stat_block_gate_reason"] = _STAT_BLOCK_GATE_REASON

    ctx.otel_span.set_attribute("tool.monster.name", args.name)
    ctx.otel_span.set_attribute("tool.monster.stat_block_included", False)
    ctx.otel_span.set_attribute("tool.monster.monster_manual_wired", True)

    return ToolResult.ok(payload)
