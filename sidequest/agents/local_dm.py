"""local_dm — DORMANT.

This module is not invoked on the live turn path as of 2026-04-28
(see docs/superpowers/specs/2026-04-28-localdm-offline-only-design.md).

It is preserved for two consumers:
  1. The offline LocalDM corpus runner (follow-up story).
  2. Re-engagement on the live path once ADR-073's local fine-tuned
     router replaces the Haiku CLI subprocess.

Unit tests for this module remain in `just check-all` so it does not
bit-rot. If you find yourself adding a live caller, you are landing
ADR-073 (or undoing this design); update both ends.

Original design: docs/superpowers/specs/2026-04-23-local-dm-decomposer-design.md
"""

from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from sidequest.agents.claude_client import ClaudeClient, LlmClient
from sidequest.genre.models.visibility import VisibilityBaseline
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
- "lethality" MUST be an empty list `[]` in Phase A. Group C wires the
  verdict producer — until then, do NOT emit lethality entries even if
  the action involves harm. The narrator handles consequence framing.
- "cross_player" stays empty in Phase A single-player decompose.
- Every dispatch REQUIRES a unique idempotency_key (string).
- Every dispatch and directive REQUIRES a full visibility tag.
- "dispatch[].subsystem" is a CLOSED ENUM. The only three legal values
  in Phase A are:
    reflect_absence / distinctive_detail_hint / npc_agency
  Do NOT invent subsystems like `character_action`, `examination`,
  `inventory_action`, `movement`, `perception`, `combat_action`, or any
  other semantic bucket. **The narrator handles every player action as
  prose** — drawing a sword, examining tally marks, lighting a torch,
  descending a rope, scouting a passage are NOT subsystem dispatches.
  Emit `dispatch: []` for any turn whose action does not specifically
  require one of the three legal subsystems above. Over-decomposing
  silently absorbs intent: the runtime logs `subsystems.unknown` and the
  mechanic never engages.
- "narrator_instructions[].kind" is a CLOSED ENUM. The only legal values
  are the four listed above:
    must_narrate / must_not_narrate /
    distinctive_detail_for_referent / canonical_only_do_not_reveal_to_others
  Do NOT invent kinds like `establish_location`, `npc_response`,
  `encounter_preparation`, `set_scene`, etc. The narrator handles scene
  framing on its own. If you need to push narrative content, encode it
  as `must_narrate` with the content in `payload`.
- Pydantic rejects any unknown kind, missing field, or extra field. A
  rejected package falls back to a degraded empty package — losing the
  per-player narrator instructions you spent tokens producing. Stay
  inside the schema.
- Output valid JSON only. No preamble. No code fences. No commentary."""


def apply_visibility_baseline(
    dispatch: dict,
    *,
    baseline: VisibilityBaseline,
    actor_player_id: str,
) -> dict:
    """Fill in VisibilityTag defaults from baseline for a decomposer dispatch dict.

    Respects explicit tags — a dispatch already flagged `_visibility_explicit: True`
    keeps whatever the decomposer chose. Called per dispatch after LLM parse.

    Unknown subsystems (not present in baseline.default_visibility) are left
    as-is — the decomposer's emission wins. This avoids silent overrides on
    subsystems the pack author didn't explicitly opine about.
    """
    if dispatch.get("_visibility_explicit"):
        return dispatch
    subsystem = dispatch.get("subsystem")
    mode = baseline.default_visibility.get(subsystem) if subsystem else None
    if mode is None:
        return dispatch  # Unknown subsystem — leave as-is (decomposer's choice stands).
    viz = dict(dispatch.get("visibility", {}))
    if mode == "actor_only":
        viz["visible_to"] = [actor_player_id]
    elif mode == "all":
        viz["visible_to"] = "all"
    # "audio_only_muffled" is a fidelity statement, not a visible_to override —
    # leave visible_to alone; fidelity handling is Task 5 ProjectionFilter territory.
    return {**dispatch, "visibility": viz}


def _normalize_multi_target_resolved_to(raw_dict: dict) -> int:
    """Count list-valued ``resolved_to`` entries in a pre-validation dict.

    Pingpong 2026-04-26 S2-OBS: the decomposer occasionally emits a
    ``list[str]`` for tokens that resolve to multiple PCs (e.g.
    ``resolved_to=['Paul','John','George','Ringo']`` for "the party").
    The schema now accepts either form (``str | list[str] | None``) so
    validation no longer fails, but we still walk the dict before
    validation so the GM panel sees how often multi-target resolution
    fires (recorded as the ``resolved_to_multi_target_count`` span
    attribute by the caller).

    Mutation-free for the value itself; counts only.

    Returns the number of list-valued ``resolved_to`` entries observed.
    """
    count = 0
    for pd in raw_dict.get("per_player", []) or []:
        for ref in pd.get("resolved", []) or []:
            value = ref.get("resolved_to") if isinstance(ref, dict) else None
            if isinstance(value, list):
                count += 1
    return count


def _apply_baseline_to_package_dict(
    raw_dict: dict,
    baseline: VisibilityBaseline,
) -> None:
    """Mutate a pre-validation DispatchPackage dict in place, defaulting
    every VisibilityTag on every dispatch / narrator_instruction / lethality
    via :func:`apply_visibility_baseline`.

    The baseline keys on `subsystem`. Narrator directives and lethality
    verdicts don't carry a subsystem, so only dispatches get baseline-driven
    defaults — directives/verdicts keep whatever the decomposer emitted.
    That matches the spec: the baseline is a per-subsystem policy.
    """
    for pd in raw_dict.get("per_player", []):
        actor = pd.get("player_id", "")
        new_dispatches = []
        for d in pd.get("dispatch", []):
            new_dispatches.append(
                apply_visibility_baseline(
                    d,
                    baseline=baseline,
                    actor_player_id=actor,
                )
            )
        pd["dispatch"] = new_dispatches


_CODE_FENCE_RE = re.compile(
    r"(?s)^\s*```(?:json)?\s*\n?(?P<body>.*?)\n?```\s*$",
)


def _extract_json_object(raw: str) -> tuple[str, list[str]]:
    """Strip common LLM wrappers off a JSON response.

    Handles three failure modes seen in production (playtest 2026-04-24):

      1. The Haiku response is wrapped in a ```json ... ``` code fence
         despite the system prompt saying "no fences". The fence breaks
         ``json.loads`` with a ``JSONDecodeError``.
      2. The response has preamble text before the first ``{`` — e.g.
         "Here is the DispatchPackage:\n{...}".
      3. Trailing commentary after the closing ``}``.

    Strategy: try a fenced-block extract first, then fall back to
    "first ``{`` through matching ``}``" balanced-brace slicing. Returns
    ``(cleaned_text, applied_steps)`` where ``applied_steps`` is a list
    of labels like ``["strip_fence", "strip_preamble"]`` suitable for
    OTEL span attributes so the GM panel can see the contract is being
    violated even when the parse ultimately succeeds.

    Silent fallbacks are forbidden by the project's No Silent Fallbacks
    rule — hence ``applied_steps``. Downstream loudly records every
    wrapper we had to peel off.
    """
    applied: list[str] = []
    text = raw.strip()

    # Step 1: strip code fences if the whole response is wrapped in one.
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match is not None:
        text = fence_match.group("body").strip()
        applied.append("strip_fence")

    # Step 2: balanced-brace slicing — find the first `{` and scan until
    # the matching `}` at depth 0. Honors quoted strings with escapes so
    # a `}` inside a string value doesn't end the scan early.
    start = text.find("{")
    if start == -1:
        return text, applied  # No object at all — let json.loads raise.
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        # Unbalanced braces — leave text as-is; json.loads will report.
        return text, applied

    if start != 0:
        applied.append("strip_preamble")
    if end != len(text) - 1:
        applied.append("strip_trailing")
    return text[start : end + 1], applied


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
    replacing the `LlmClient` client injection.

    Stateless per turn — every call sends the full system prompt with a
    fresh session id, so the closed-enum and ``lethality=[]`` constraints
    are restated every turn instead of relying on resumed-session memory.
    See module docstring for the playtest evidence behind the stateless
    fallback (spec §461).
    """

    def __init__(self, client: LlmClient | None = None) -> None:
        self._client: LlmClient = client if client is not None else ClaudeClient()

    def reset_session(self) -> None:
        """No-op kept for callers that haven't migrated off the stateful API.

        LocalDM is stateless per turn — there is no cached session id to
        reset. Method retained so external resets (e.g., on snapshot
        rollback) don't fail with AttributeError.
        """
        return None

    async def decompose(
        self,
        *,
        turn_id: str,
        player_id: str,
        raw_action: str,
        state_summary: str,
        visibility_baseline: VisibilityBaseline | None = None,
    ) -> DispatchPackage:
        """Decompose one player action into a DispatchPackage.

        On any failure returns a degraded=True package per spec §6.6 —
        the table never blocks.

        If ``visibility_baseline`` is provided, every dispatch /
        narrator_instruction / lethality verdict in the parsed package has
        its VisibilityTag defaulted via :func:`apply_visibility_baseline`
        before DispatchPackage validation. Explicit tags (future:
        decomposer output marked ``_visibility_explicit``) are preserved.
        """
        with local_dm_decompose_span(
            turn_id=turn_id,
            player_id=player_id,
            action_len=len(raw_action),
        ) as span:
            user_prompt = _build_user_prompt(turn_id, player_id, raw_action, state_summary)

            try:
                # Stateless per turn — always pass system_prompt and let
                # send_with_session allocate a fresh session id. Spec §461
                # fallback after drift surfaced in playtest 2026-04-26.
                response = await self._client.send_with_session(
                    prompt=user_prompt,
                    model=DECOMPOSER_MODEL,
                    session_id=None,
                    system_prompt=_DECOMPOSER_SYSTEM_PROMPT,
                    allowed_tools=[],
                    env_vars={},
                )
            except Exception as exc:  # TimeoutError, subprocess failure, whatever.
                logger.warning("local_dm.client_exception turn_id=%s exc=%s", turn_id, exc)
                reason = f"client_exception: {exc}"
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", reason)
                return _degraded_package(turn_id, reason=reason)

            raw_text = (response.text or "").strip()
            if not raw_text:
                logger.warning("local_dm.empty_response turn_id=%s", turn_id)
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", "empty_response")
                return _degraded_package(turn_id, reason="empty_response")

            # Peel common LLM wrappers (code fences, preamble, trailing
            # commentary) off the response before json.loads. Every peel
            # is recorded as a span attribute so the GM panel can see when
            # Haiku is violating the "JSON only, no fences" contract —
            # silent recovery would mask a prompt regression.
            cleaned_text, cleanup_steps = _extract_json_object(raw_text)
            if cleanup_steps:
                span.set_attribute(
                    "json_cleanup_steps",
                    ",".join(cleanup_steps),
                )
                logger.info(
                    "local_dm.json_cleanup turn_id=%s steps=%s",
                    turn_id,
                    cleanup_steps,
                )

            try:
                # Parse to dict first so we can apply baseline defaults to
                # VisibilityTags before Pydantic validation locks them in.
                import json as _json

                raw_dict = _json.loads(cleaned_text)
                # Pingpong 2026-04-26 S2-OBS: count list-valued
                # ``resolved_to`` entries so the GM panel sees when
                # multi-target resolution fires (previously these crashed
                # validation and silently degraded the package). The
                # schema now accepts ``str | list[str] | None``.
                multi_target_count = _normalize_multi_target_resolved_to(raw_dict)
                if multi_target_count:
                    span.set_attribute(
                        "resolved_to_multi_target_count", multi_target_count,
                    )
                    logger.info(
                        "local_dm.multi_target_resolved_to turn_id=%s count=%d",
                        turn_id, multi_target_count,
                    )
                if visibility_baseline is not None:
                    _apply_baseline_to_package_dict(raw_dict, visibility_baseline)
                pkg = DispatchPackage.model_validate(raw_dict)
            except ValidationError as exc:
                logger.warning("local_dm.parse_failure turn_id=%s exc=%s", turn_id, exc)
                reason = f"parse_failure: {type(exc).__name__}"
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", reason)
                return _degraded_package(turn_id, reason=reason)
            except (ValueError, TypeError) as exc:  # json.JSONDecodeError is ValueError
                logger.warning(
                    "local_dm.parse_failure turn_id=%s exc=%s preview=%r",
                    turn_id,
                    exc,
                    cleaned_text[:160],
                )
                reason = f"parse_failure: {type(exc).__name__}"
                span.set_attribute("degraded", True)
                span.set_attribute("degraded_reason", reason)
                # Surface a short preview so the GM panel can see what
                # Haiku actually returned instead of guessing.
                span.set_attribute("parse_preview", cleaned_text[:160])
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
