"""LocalDM — structured-output decomposer between sealed-letter and narrator.

Spec: docs/superpowers/specs/2026-04-23-local-dm-decomposer-design.md §3-§7

Reads player action + game state. Emits DispatchPackage (spec §5).
Never writes prose. Runs on a persistent Haiku session (ADR-066 pattern).

Group B scope: single-player decompose. Multiplayer batching lands in
Group G alongside the multiplayer session model wiring.

On any parse failure, LLM timeout, or unexpected exception, emits a
degraded=True package (spec §6.6) — the table never blocks.
"""
from __future__ import annotations

import logging
from threading import Lock

from pydantic import ValidationError

from sidequest.agents.claude_client import ClaudeClient, ClaudeLike
from sidequest.protocol.dispatch import DispatchPackage
from sidequest.telemetry.spans import local_dm_decompose_span

logger = logging.getLogger(__name__)

DECOMPOSER_MODEL = "haiku"

# Advertised subsystem vocabulary for Phase A. New subsystems are added to
# this list as they land (Groups C-G). The subsystem registry (Task 7)
# is the runtime authority; this is documentation for the prompt.
KNOWN_SUBSYSTEMS: tuple[str, ...] = (
    "reflect_absence",
    "distinctive_detail_hint",
    "npc_agency",
)


_DECOMPOSER_SYSTEM_PROMPT = """You are the Local DM — an impartial structured-output reader.

Your job: read a player's action + the game state, then emit ONE JSON object
matching the DispatchPackage schema. Never write prose. Never call tools.
Output JSON only — no preamble, no explanation, no markdown fences.

You are fair to NPC agendas, fair to genre lethality, fair to physics,
fair to the player — in that order when they conflict. You do not soften
outcomes to spare the player. You do not invent hostile outcomes the state
doesn't warrant. Impartiality cuts both ways.

For each player action:
  1. Resolve referents (pronouns, ellipses, demonstratives). Every resolution
     carries a confidence 0.0-1.0 and plausible alternatives. If nothing
     plausibly resolves, set resolved_to=null with confidence=0 — do NOT
     invent a filler.
  2. Emit subsystem dispatches. Known subsystems:
       - reflect_absence — use when an addressee is unresolved; do not invent
         followers.
       - distinctive_detail_hint — use when a referent is ambiguous; provide
         a distinctive detail (e.g., "broken tooth") so the narrator names
         the target cleanly.
       - npc_agency — use when an NPC needs to decide or react.
  3. Emit narrator_instructions — must_narrate / must_not_narrate /
     distinctive_detail_for_referent / canonical_only_do_not_reveal_to_others.
  4. Set confidence_global to your overall confidence across the turn.

Every dispatch carries a visibility tag. For Phase A, emit
visible_to="all" with empty perception_fidelity unless the state clearly
names asymmetric visibility.

OUTPUT: exactly one JSON object, DispatchPackage shape. No other text.

OUTPUT SHAPE (exact field names required; Pydantic rejects unknown fields):

{
  "turn_id": "<string>",
  "per_player": [
    {
      "player_id": "<string>",
      "raw_action": "<string>",
      "resolved": [
        {"token": "<string>", "resolved_to": "<entity-id>|null", "confidence": 0.0, "alternatives": [], "resolution_note": null}
      ],
      "dispatch": [
        {"subsystem": "reflect_absence|distinctive_detail_hint|npc_agency",
         "params": {}, "depends_on": [], "idempotency_key": "<unique-string>",
         "visibility": {"visible_to": "all", "perception_fidelity": {}, "secrets_for": [], "redact_from_narrator_canonical": false}}
      ],
      "lethality": [],
      "narrator_instructions": [
        {"kind": "must_narrate|must_not_narrate|distinctive_detail_for_referent|canonical_only_do_not_reveal_to_others",
         "payload": "<string>",
         "visibility": {"visible_to": "all", "perception_fidelity": {}, "secrets_for": [], "redact_from_narrator_canonical": false}}
      ]
    }
  ],
  "cross_player": [],
  "confidence_global": 0.0,
  "degraded": false,
  "degraded_reason": null
}

RULES:
- "lethality" stays empty in Phase A (Group C wires the verdict producer).
- "cross_player" stays empty in Phase A single-player decompose.
- Every dispatch REQUIRES a unique idempotency_key (string).
- Every dispatch and directive REQUIRES a full visibility tag.
- Output valid JSON only. No preamble. No code fences. No commentary."""


def _build_user_prompt(turn_id: str, player_id: str, raw_action: str, state_summary: str) -> str:
    return (
        f"turn_id: {turn_id}\n"
        f"player_id: {player_id}\n"
        f"<game_state>\n{state_summary}\n</game_state>\n"
        f"<raw_action>\n{raw_action}\n</raw_action>\n"
        f"Emit DispatchPackage JSON for this single action."
    )


class LocalDM:
    """Local DM decomposer.

    Haiku-backed in Group B; swap for local fine-tune in Group E by
    replacing the `ClaudeLike` client injection.
    """

    def __init__(self, client: ClaudeLike | None = None) -> None:
        self._client: ClaudeLike = client if client is not None else ClaudeClient()
        self._session_id: str | None = None
        self._session_lock: Lock = Lock()

    def reset_session(self) -> None:
        """Clear the persistent session id (ADR-066 reset semantics)."""
        with self._session_lock:
            self._session_id = None

    async def decompose(
        self,
        *,
        turn_id: str,
        player_id: str,
        raw_action: str,
        state_summary: str,
    ) -> DispatchPackage:
        """Decompose one player action into a DispatchPackage.

        On any failure returns a degraded=True package per spec §6.6 —
        the table never blocks.
        """
        with local_dm_decompose_span(
            turn_id=turn_id,
            player_id=player_id,
            action_len=len(raw_action),
        ) as span:
            user_prompt = _build_user_prompt(turn_id, player_id, raw_action, state_summary)

            with self._session_lock:
                current_session = self._session_id

            try:
                response = await self._client.send_with_session(
                    prompt=user_prompt,
                    model=DECOMPOSER_MODEL,
                    session_id=current_session,
                    system_prompt=_DECOMPOSER_SYSTEM_PROMPT if current_session is None else None,
                    allowed_tools=[],
                    env_vars={},
                )
            except Exception as exc:  # TimeoutError, subprocess failure, whatever.
                logger.warning("local_dm.client_exception turn_id=%s exc=%s", turn_id, exc)
                # Stale session id after transport/auth failure would produce an
                # infinite degraded loop next turn. Reset so the next call establishes.
                with self._session_lock:
                    self._session_id = None
                reason = f"client_exception: {exc}"
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", reason)
                return _degraded_package(turn_id, reason=reason)

            # Cache session id after first successful call.
            if response.session_id:
                with self._session_lock:
                    self._session_id = response.session_id

            raw_text = (response.text or "").strip()
            if not raw_text:
                logger.warning("local_dm.empty_response turn_id=%s", turn_id)
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", "empty_response")
                return _degraded_package(turn_id, reason="empty_response")

            try:
                pkg = DispatchPackage.model_validate_json(raw_text)
            except ValidationError as exc:
                logger.warning("local_dm.parse_failure turn_id=%s exc=%s", turn_id, exc)
                reason = f"parse_failure: {type(exc).__name__}"
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", reason)
                return _degraded_package(turn_id, reason=reason)

            span.set_attribute("degraded", pkg.degraded)
            if pkg.degraded:
                span.set_attribute("degraded_reason", pkg.degraded_reason or "")
            return pkg


def _degraded_package(turn_id: str, *, reason: str) -> DispatchPackage:
    return DispatchPackage(
        turn_id=turn_id,
        per_player=[],
        cross_player=[],
        confidence_global=0.0,
        degraded=True,
        degraded_reason=reason,
    )


__all__ = ["DECOMPOSER_MODEL", "KNOWN_SUBSYSTEMS", "LocalDM"]
