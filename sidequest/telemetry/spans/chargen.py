"""Character generation spans — stat rolls, backstory, archetype gate."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute

SPAN_CHARGEN_STAT_ROLL = "chargen.stat_roll"
SPAN_CHARGEN_STATS_GENERATED = "chargen.stats_generated"
SPAN_CHARGEN_BACKSTORY_COMPOSED = "chargen.backstory_composed"

FLAT_ONLY_SPANS.update({
    SPAN_CHARGEN_STAT_ROLL,
    SPAN_CHARGEN_STATS_GENERATED,
    SPAN_CHARGEN_BACKSTORY_COMPOSED,
})


# ---------------------------------------------------------------------------
# Chargen archetype-resolution gate (Story 45-6).
#
# The chargen-confirmation seam wraps ``_resolve_character_archetype`` with a
# gate that distinguishes three states:
#
# - ``ok_resolved``      — resolver wrote a display name (pass).
# - ``ok_no_axes``       — pack opted out of the archetype system (pass).
# - ``blocked_partial``  — character would ship with a raw ``"j/r"`` pair, a
#                          missing-axes-with-pack-axes mismatch, or a
#                          resolver-raised state (fail; chargen rejected).
#
# The evaluator span fires on every chargen-confirm so Sebastien's GM panel
# (CLAUDE.md "OTEL Observability Principle") gets the negative confirmation
# that the gate ran. The blocked span fires only on the failure branch and
# carries ``block_reason`` so the dashboard can distinguish the three
# pumblestone-style failure modes.
# ---------------------------------------------------------------------------
SPAN_CHARGEN_ARCHETYPE_GATE_EVALUATED = "chargen.archetype_gate_evaluated"
SPAN_ROUTES[SPAN_CHARGEN_ARCHETYPE_GATE_EVALUATED] = SpanRoute(
    event_type="state_transition",
    component="character_creation",
    extract=lambda span: {
        "field": "archetype_gate",
        "op": "evaluated",
        "state": (span.attributes or {}).get("state", ""),
        "resolved_archetype": (span.attributes or {}).get("resolved_archetype", ""),
        "pack_has_axes": (span.attributes or {}).get("pack_has_axes", False),
        # Per-pair granularity: the gate has no access to the builder
        # accumulator post-build, so ``had_both_hints`` is the most
        # granular signal it can emit. False ⇒ at least one of jungian
        # or rpg_role hint was missing during chargen.
        "had_both_hints": (span.attributes or {}).get("had_both_hints", False),
        "provenance_set": (span.attributes or {}).get("provenance_set", False),
        "genre": (span.attributes or {}).get("genre", ""),
        "world": (span.attributes or {}).get("world", ""),
        "player_id": (span.attributes or {}).get("player_id", ""),
    },
)
SPAN_CHARGEN_ARCHETYPE_GATE_BLOCKED = "chargen.archetype_gate_blocked"
SPAN_ROUTES[SPAN_CHARGEN_ARCHETYPE_GATE_BLOCKED] = SpanRoute(
    event_type="state_transition",
    component="character_creation",
    extract=lambda span: {
        "field": "archetype_gate",
        "op": "blocked",
        "state": "blocked_partial",
        "block_reason": (span.attributes or {}).get("block_reason", ""),
        "resolved_archetype": (span.attributes or {}).get("resolved_archetype", ""),
        "pack_has_axes": (span.attributes or {}).get("pack_has_axes", False),
        "had_both_hints": (span.attributes or {}).get("had_both_hints", False),
        "provenance_set": (span.attributes or {}).get("provenance_set", False),
        "genre": (span.attributes or {}).get("genre", ""),
        "world": (span.attributes or {}).get("world", ""),
        "player_id": (span.attributes or {}).get("player_id", ""),
    },
)
