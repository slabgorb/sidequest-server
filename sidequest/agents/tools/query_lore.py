"""Tool: query_lore — narrator-private RAG against the world LoreStore.

Phase C Task 13 — read tool, no perception rule (yet)
-----------------------------------------------------
ADR-048 (Lore RAG Store) introduced an in-memory indexed
:class:`~sidequest.game.lore_store.LoreStore` that holds world lore
fragments — genre-pack imports, character-creation backstory entries,
and game-event accretions. The narrator queries it before a turn to
ground its prose in the world's authored detail.

Phase B amendment
~~~~~~~~~~~~~~~~~
:class:`~sidequest.agents.tool_registry.ToolContext` was extended with
an optional ``lore_store: LoreStore | None`` field for this tool. The
:class:`LoreStore` lives on
:class:`~sidequest.server.session_handler.SessionHandler`, not on the
:class:`~sidequest.game.persistence.SqliteStore` save layer, so it is
not reachable via ``ctx.store``. Production wiring (constructing the
ctx with the session-handler's LoreStore) is Phase E.

When ``ctx.lore_store is None`` (e.g. during tests, or before Phase E
lands), the tool returns an empty result and stamps the dispatch span
with ``tool.lore.lore_store_wired = False`` so the GM panel can detect
un-wired calls. This is intentionally NOT a hard error — Phase E will
flip it on without revisiting Phase C tools.

v1 simplifications
~~~~~~~~~~~~~~~~~~
* **Keyword substring search.**
  :meth:`LoreStore.query_by_similarity` requires a precomputed query
  embedding the narrator does not currently produce. v1 falls back to
  :meth:`LoreStore.query_by_keyword` (case-insensitive substring).
  Phase D may wire embed-on-the-fly via
  :class:`~sidequest.daemon_client.DaemonClient` if profiling warrants
  the round-trip.
* **No perception rule.** The plan's "hide classified/secret unless
  the PC has the secret-tag" rule needs a PC tag system that doesn't
  exist yet — deferred to Phase D alongside the embedding pipeline.
* **Dropped ``tool.lore.top_score`` from plan.** Without embedding
  similarity there is no score to report. Replaced by
  ``tool.lore.lore_store_wired`` (bool) so the GM panel can distinguish
  "wired and empty" from "not wired yet".

Payload
~~~~~~~
Each fragment dict: ``{id, category, content, source, turn_created,
metadata}``. The dispatch payload also carries ``k`` (the requested
cap) and ``lore_store_wired`` (the Phase E wiring marker).

OTEL
~~~~
* ``tool.lore.k`` — the requested cap (1-20).
* ``tool.lore.hit_count`` — number of fragments returned.
* ``tool.lore.lore_store_wired`` — bool. ``False`` when Phase E has
  not yet wired the production ctx; ``True`` otherwise.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sidequest.agents.tool_registry import (
    ToolCategory,
    ToolContext,
    ToolResult,
    tool,
)


class QueryLoreArgs(BaseModel):
    topic_or_query: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-text query. v1 falls back to substring keyword matching "
            "(case-insensitive) against fragment content."
        ),
    )
    k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of fragments to return.",
    )


@tool(
    name="query_lore",
    description=(
        "RAG query against the world lore store. Returns top-k matching lore "
        "entries with embedding similarity."
    ),
    category=ToolCategory.READ,
)
async def query_lore(args: QueryLoreArgs, ctx: ToolContext) -> ToolResult:
    if ctx.lore_store is None:
        # Phase E wires the LoreStore into ToolContext at the production
        # call site. Until then, return an empty result with an OTEL marker
        # so the GM panel can see the tool fired but lore wasn't reachable.
        ctx.otel_span.set_attribute("tool.lore.k", args.k)
        ctx.otel_span.set_attribute("tool.lore.hit_count", 0)
        ctx.otel_span.set_attribute("tool.lore.lore_store_wired", False)
        return ToolResult.ok(
            {
                "fragments": [],
                "k": args.k,
                "lore_store_wired": False,
            }
        )

    # v1: keyword substring search. embedding-based query_by_similarity needs
    # a precomputed query embedding the narrator doesn't currently produce.
    hits = ctx.lore_store.query_by_keyword(args.topic_or_query)
    hits = hits[: args.k]

    fragments = [
        {
            "id": f.id,
            "category": f.category,
            "content": f.content,
            "source": f.source,
            "turn_created": f.turn_created,
            "metadata": dict(f.metadata) if f.metadata else {},
        }
        for f in hits
    ]

    ctx.otel_span.set_attribute("tool.lore.k", args.k)
    ctx.otel_span.set_attribute("tool.lore.hit_count", len(fragments))
    ctx.otel_span.set_attribute("tool.lore.lore_store_wired", True)

    return ToolResult.ok(
        {
            "fragments": fragments,
            "k": args.k,
            "lore_store_wired": True,
        }
    )
