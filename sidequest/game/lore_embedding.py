"""Lore embedding worker and RAG retrieval — Story 37-33.

Wires :class:`sidequest.daemon_client.DaemonClient.embed` into the
lore pipeline:

- :func:`embed_pending_fragments` drains fragments whose
  ``embedding_pending`` flag is set, calling the daemon once per
  fragment and writing back the resulting vector via
  :meth:`LoreStore.update_embedding`.
- :func:`retrieve_lore_context` embeds the player's action (or any
  query text) and returns the top-k most similar fragments formatted
  as a prompt section for the narrator.

Both helpers degrade gracefully when the daemon is unavailable or
returns a structured error — the narrator always runs, it just runs
without the RAG context injection on that turn. CLAUDE.md's "No
Silent Fallbacks" rule applies to the behavioural contract, not to
optional-sidecar unavailability: daemon absence is logged loudly and
surfaced through OTEL attributes, never masked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from opentelemetry import trace

from sidequest.daemon_client import (
    DaemonClient,
    DaemonRequestError,
    DaemonUnavailableError,
)
from sidequest.game.lore_store import LoreFragment, LoreStore

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("sidequest.game.lore_embedding")

DEFAULT_MAX_RETRIES = 3
"""Fragments past this retry count are left alone until a future
worker run raises the ceiling or a human intervenes. Keeps a single
poisoned fragment from burning through embed budget every turn."""

DEFAULT_RETRIEVAL_TOP_K = 3
"""Conservative default — RAG results land in the narrator prompt's
Valley zone; beyond ~3 fragments the narrator starts hallucinating
connections that weren't in the query."""

DEFAULT_RETRIEVAL_MIN_SIMILARITY = 0.15
"""Cosine similarity floor for retrieval. MiniLM produces positive
cosine values even for unrelated strings; 0.15 drops obvious noise
while keeping legitimately tangential fragments."""

DEFAULT_FRAGMENT_PREVIEW_CHARS = 240
"""How much of each retrieved fragment to inline into the prompt.
Full fragments would blow the Valley budget — the narrator gets a
preview and can ask the player for more via the story beat."""


# ---------------------------------------------------------------------------
# Embedding worker
# ---------------------------------------------------------------------------


@dataclass
class EmbedWorkerResult:
    """Telemetry for one :func:`embed_pending_fragments` run."""

    embedded: int = 0
    failed: int = 0
    skipped_daemon_unavailable: bool = False
    skipped_empty_queue: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "embedded": self.embedded,
            "failed": self.failed,
            "skipped_daemon_unavailable": self.skipped_daemon_unavailable,
            "skipped_empty_queue": self.skipped_empty_queue,
        }


async def embed_pending_fragments(
    lore_store: LoreStore,
    client: DaemonClient | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_per_run: int | None = None,
) -> EmbedWorkerResult:
    """Embed fragments with ``embedding_pending=True`` via the daemon.

    Intended to run as a fire-and-forget background task after a
    narration turn. Returns an :class:`EmbedWorkerResult` for OTEL
    emission by the caller.

    If ``client`` is ``None`` a fresh :class:`DaemonClient` is built
    with defaults. If the socket is absent the run returns early with
    ``skipped_daemon_unavailable=True`` — no attempt is made to
    connect, matching the render dispatch pattern.
    """
    result = EmbedWorkerResult()
    with tracer.start_as_current_span("lore_embedding.worker") as span:
        pending = lore_store.pending_embedding_ids(max_retries=max_retries)
        span.set_attribute("lore.pending_count", len(pending))
        if not pending:
            result.skipped_empty_queue = True
            span.set_attribute("lore.skipped", "empty_queue")
            return result

        if client is None:
            client = DaemonClient()
        if not client.is_available():
            result.skipped_daemon_unavailable = True
            span.set_attribute("lore.skipped", "daemon_unavailable")
            logger.warning(
                "lore_embedding.worker skipped reason=daemon_unavailable "
                "pending=%d socket=%s",
                len(pending),
                client.socket_path,
            )
            return result

        if max_per_run is not None:
            pending = pending[: max(0, max_per_run)]
            span.set_attribute("lore.max_per_run", max_per_run)

        for frag_id in pending:
            frag = lore_store.fragments.get(frag_id)
            if frag is None:
                # Dropped between pending_ids() and now — skip silently,
                # counter stays untouched.
                continue
            try:
                response = await client.embed(frag.content)
            except DaemonUnavailableError as exc:
                # Daemon went away mid-run. Stop the loop loudly — the
                # remaining fragments stay pending for the next turn.
                logger.warning(
                    "lore_embedding.worker daemon_unavailable mid_run "
                    "fragment=%s remaining=%d error=%s",
                    frag_id,
                    len(pending) - result.embedded - result.failed,
                    exc,
                )
                span.set_attribute(
                    "lore.early_exit", "daemon_unavailable_mid_run"
                )
                result.skipped_daemon_unavailable = True
                break
            except DaemonRequestError as exc:
                lore_store.mark_embedding_failed(frag_id)
                result.failed += 1
                logger.warning(
                    "lore_embedding.worker embed_failed fragment=%s "
                    "code=%s message=%s retry_count=%d",
                    frag_id,
                    exc.code,
                    exc.message,
                    frag.embedding_retry_count,
                )
                continue
            except ValueError as exc:
                # MAX_EMBED_BYTES guard in the client — the fragment is
                # too large to embed. Mark as failed so we stop trying.
                lore_store.mark_embedding_failed(frag_id)
                result.failed += 1
                logger.warning(
                    "lore_embedding.worker text_too_large fragment=%s "
                    "content_bytes=%d error=%s",
                    frag_id,
                    len(frag.content.encode("utf-8")),
                    exc,
                )
                continue

            lore_store.update_embedding(frag_id, response["embedding"])
            result.embedded += 1

        span.set_attribute("lore.embedded", result.embedded)
        span.set_attribute("lore.failed", result.failed)
        return result


# ---------------------------------------------------------------------------
# RAG retrieval
# ---------------------------------------------------------------------------


async def retrieve_lore_context(
    lore_store: LoreStore,
    query_text: str,
    client: DaemonClient | None = None,
    *,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    min_similarity: float = DEFAULT_RETRIEVAL_MIN_SIMILARITY,
    preview_chars: int = DEFAULT_FRAGMENT_PREVIEW_CHARS,
) -> str | None:
    """Embed ``query_text``, find top-k similar fragments, format them
    for injection into the narrator prompt's Valley zone.

    Returns ``None`` when there is nothing useful to inject (empty
    store, daemon unavailable, embed failure, no fragments above the
    similarity floor). The caller passes ``None`` straight through
    to :class:`TurnContext.lore_context` so no prompt section is
    registered — keeps the prompt zone-clean instead of leaking an
    empty ``<lore>`` block.

    Never raises. All failure paths are logged and return ``None``.
    """
    with tracer.start_as_current_span("lore_embedding.retrieve") as span:
        span.set_attribute("lore.query_len", len(query_text))
        span.set_attribute("lore.store_size", len(lore_store))

        if not query_text.strip() or lore_store.is_empty():
            span.set_attribute("lore.outcome", "empty_query_or_store")
            return None

        if client is None:
            client = DaemonClient()
        if not client.is_available():
            span.set_attribute("lore.outcome", "daemon_unavailable")
            logger.info(
                "lore_embedding.retrieve skipped reason=daemon_unavailable"
            )
            return None

        try:
            response = await client.embed(query_text)
        except (DaemonUnavailableError, DaemonRequestError) as exc:
            span.set_attribute("lore.outcome", "embed_failed")
            span.set_attribute("lore.error_type", type(exc).__name__)
            logger.warning("lore_embedding.retrieve embed_failed error=%s", exc)
            return None
        except ValueError as exc:
            # Query text exceeded MAX_EMBED_BYTES — truncate? No: the
            # caller should trim before calling. Log and bail.
            span.set_attribute("lore.outcome", "query_too_large")
            logger.warning(
                "lore_embedding.retrieve query_too_large len=%d error=%s",
                len(query_text),
                exc,
            )
            return None

        hits = lore_store.query_by_similarity(response["embedding"], top_k=top_k)
        hits = [(sim, frag) for sim, frag in hits if sim >= min_similarity]
        span.set_attribute("lore.hit_count", len(hits))

        if not hits:
            span.set_attribute("lore.outcome", "no_hits_above_threshold")
            return None

        span.set_attribute(
            "lore.top_similarity", hits[0][0] if hits else 0.0
        )
        span.set_attribute("lore.outcome", "ok")
        return _format_lore_section(hits, preview_chars=preview_chars)


def _format_lore_section(
    hits: list[tuple[float, LoreFragment]],
    *,
    preview_chars: int,
) -> str:
    """Render the top-k hits as a ``<lore>`` prompt block.

    Format is intentionally plain markdown-ish — the narrator sees
    category, similarity score, and a content preview. The score is
    included so the narrator can weight confidence when weaving the
    fragment into the prose (high-score fragment = canon reference,
    low-score = tangential nudge).
    """
    lines = ["<lore>", "# Relevant lore retrieved for this turn"]
    for sim, frag in hits:
        preview = frag.content.strip().replace("\n", " ")
        if len(preview) > preview_chars:
            preview = preview[: preview_chars - 1].rstrip() + "…"
        lines.append(
            f"- [{frag.category} · id={frag.id} · similarity={sim:.2f}] {preview}"
        )
    lines.append("</lore>")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_FRAGMENT_PREVIEW_CHARS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRIEVAL_MIN_SIMILARITY",
    "DEFAULT_RETRIEVAL_TOP_K",
    "EmbedWorkerResult",
    "embed_pending_fragments",
    "retrieve_lore_context",
]
