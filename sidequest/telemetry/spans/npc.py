"""NPC registry spans — auto-registration and identity drift."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

from ._core import FLAT_ONLY_SPANS, SPAN_ROUTES, SpanRoute
from .span import Span

# Port-artifact constants — kept flat-only.
SPAN_NPC_MERGE_PATCH = "npc_merge_patch"
SPAN_NPC_REGISTRATION = "npc.registration"

FLAT_ONLY_SPANS.update({SPAN_NPC_MERGE_PATCH, SPAN_NPC_REGISTRATION})

# Live spans (NPC bundle).
SPAN_NPC_AUTO_REGISTERED = "npc.auto_registered"
SPAN_ROUTES[SPAN_NPC_AUTO_REGISTERED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "auto_registered",
        "name": (span.attributes or {}).get("npc_name", ""),
        "pronouns": (span.attributes or {}).get("pronouns", ""),
        "role": (span.attributes or {}).get("role", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "registry_len": (span.attributes or {}).get("registry_len", 0),
    },
)
# Playtest 2026-04-29: PC names appearing in narration must NOT promote the PC
# into the NPC registry. The skip span lets the GM panel verify the filter
# fired (and surfaces "the narrator named your party member" events for
# Sebastien's mechanical visibility).
SPAN_NPC_PC_NAME_SKIPPED = "npc.pc_name_skipped"
SPAN_ROUTES[SPAN_NPC_PC_NAME_SKIPPED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "pc_name_skipped",
        "name": (span.attributes or {}).get("npc_name", ""),
        "matched_pc": (span.attributes or {}).get("matched_pc", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

SPAN_NPC_REINVENTED = "npc.reinvented"
SPAN_ROUTES[SPAN_NPC_REINVENTED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "reinvented",
        "name": (span.attributes or {}).get("npc_name", ""),
        "drift_field": (span.attributes or {}).get("drift_field", ""),
        "expected": (span.attributes or {}).get("expected", ""),
        "narrator": (span.attributes or {}).get("narrator", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

# Wave 2A (story 45-47): every narrator-cite of an NPC name fires this span.
# ``match_strategy`` is the lie-detector dial: ``npcs_hit`` means the cited
# name had an active stateful Npc; ``pool_hit`` means the cite matched a
# known pool member; ``invented`` means the narrator made up a name not in
# either store. Per-session counts of ``invented`` tell the GM panel when
# the pool wasn't deep enough or wasn't seeded for the scene.
SPAN_NPC_REFERENCED = "npc.referenced"
SPAN_ROUTES[SPAN_NPC_REFERENCED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_pool",
        "op": "referenced",
        "name": (span.attributes or {}).get("npc_name", ""),
        "match_strategy": (span.attributes or {}).get("match_strategy", ""),
        "pool_origin": (span.attributes or {}).get("pool_origin", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)

# Story 45-53: recurring-presence detector — fires when narration prose
# names a known recurring NPC (in snapshot.npcs or snapshot.npc_pool) but
# the narrator failed to emit them in npcs_present. The lie-detector
# signal: per-session counts of these misses tell the GM panel when the
# narrator is "forgetting" recurring characters between turns.
SPAN_NPC_RECURRING_PRESENCE_MISSED = "npc.recurring_presence_missed"
SPAN_ROUTES[SPAN_NPC_RECURRING_PRESENCE_MISSED] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        # ``field`` mirrors ``source``: stateful Npc misses surface as
        # ``npc_registry`` (parallel to npc.auto_registered / npc.reinvented),
        # pool-only misses surface as ``npc_pool`` (parallel to npc.referenced).
        # The GM panel filters on ``field``; mis-routing would put npcs-sourced
        # misses in the wrong column.
        "field": "npc_registry"
        if (span.attributes or {}).get("source") == "npcs"
        else "npc_pool",
        "op": "recurring_presence_missed",
        "name": (span.attributes or {}).get("npc_name", ""),
        "source": (span.attributes or {}).get("source", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
        "last_seen_turn": (span.attributes or {}).get("last_seen_turn", 0),
    },
)

# Story 45-21: combat-stats write into npc_registry entry.
# Fired when an encounter handshake (or other combat-stats emit) publishes
# HP/max_hp into a registry entry so HP-check subsystems can see real data
# instead of the always-zero shape that made the Crawling Scavenger appear
# dead for all of Playtest 3.
SPAN_NPC_REGISTRY_HP_SET = "npc_registry.hp_set"
SPAN_ROUTES[SPAN_NPC_REGISTRY_HP_SET] = SpanRoute(
    event_type="state_transition",
    component="npc_registry",
    extract=lambda span: {
        "field": "npc_registry",
        "op": "hp_set",
        "name": (span.attributes or {}).get("npc_name", ""),
        "hp": (span.attributes or {}).get("hp", 0),
        "max_hp": (span.attributes or {}).get("max_hp", 0),
        "source": (span.attributes or {}).get("source", ""),
        "turn_number": (span.attributes or {}).get("turn_number", 0),
    },
)


@contextmanager
def npc_auto_registered_span(
    *,
    npc_name: str,
    pronouns: str,
    role: str,
    turn_number: int,
    registry_len: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Attribute key ``npc_name`` avoids the OTEL span ``name`` reserved attribute."""
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "pronouns": pronouns,
        "role": role,
        "turn_number": turn_number,
        "registry_len": registry_len,
        **attrs,
    }
    with Span.open(SPAN_NPC_AUTO_REGISTERED, attributes, tracer_override=_tracer) as span:
        yield span


@contextmanager
def npc_referenced_span(
    *,
    npc_name: str,
    match_strategy: str,
    pool_origin: str | None,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Wave 2A (story 45-47): emitted on every narrator-cite of an NPC name.

    ``match_strategy`` is the lie-detector dial — ``npcs_hit`` means the
    name resolved to an active stateful ``Npc``; ``pool_hit`` means it
    matched a known ``NpcPoolMember``; ``invented`` means the narrator
    made up a name not in either store. Per-session counts of
    ``invented`` tell the GM panel when the pool wasn't seeded for the
    scene.

    ``pool_origin`` is the ``NpcPoolMember.name`` the resulting/existing
    ``Npc`` was promoted from, or ``None`` for narrator-invented or
    legacy-without-provenance NPCs.
    """
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "match_strategy": match_strategy,
        "pool_origin": pool_origin if pool_origin is not None else "",
        "turn_number": turn_number,
        **attrs,
    }
    with Span.open(
        SPAN_NPC_REFERENCED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def npc_pc_name_skipped_span(
    *,
    npc_name: str,
    matched_pc: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Emitted when narration tries to auto-register a PC's name as an NPC.

    ``matched_pc`` is the canonical PC name we matched against (case-folded
    equality on ``character.core.name``). The span MUST fire whenever the
    filter triggers — it's the only way Sebastien's GM panel can see that
    the narrator is naming party members in NPC-registry contexts (one of
    the symptoms behind the playtest 2026-04-29 "narrator confused PC for
    NPC" report).
    """
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "matched_pc": matched_pc,
        "turn_number": turn_number,
        **attrs,
    }
    with Span.open(
        SPAN_NPC_PC_NAME_SKIPPED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def npc_registry_hp_set_span(
    *,
    npc_name: str,
    hp: int,
    max_hp: int,
    source: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Story 45-21: emitted when combat stats are written into a registry entry.

    ``source`` labels which subsystem published the stats (e.g.
    ``encounter_handshake``, ``apply_beat``). Allows the GM panel to
    verify the registry-write seam is firing and not silently dropping.
    """
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "hp": hp,
        "max_hp": max_hp,
        "source": source,
        "turn_number": turn_number,
        **attrs,
    }
    with Span.open(
        SPAN_NPC_REGISTRY_HP_SET,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span


@contextmanager
def npc_reinvented_span(
    *,
    npc_name: str,
    drift_field: str,
    expected: str,
    narrator: str,
    turn_number: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """``severity="warning"`` so the WatcherSpanProcessor renders this as a drift alert."""
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "drift_field": drift_field,
        "expected": expected,
        "narrator": narrator,
        "turn_number": turn_number,
        "severity": "warning",
        **attrs,
    }
    with Span.open(SPAN_NPC_REINVENTED, attributes, tracer_override=_tracer) as span:
        yield span


@contextmanager
def npc_recurring_presence_missed_span(
    *,
    npc_name: str,
    source: str,
    turn_number: int,
    last_seen_turn: int,
    _tracer: trace.Tracer | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Story 45-53: emitted when narration prose names a known recurring
    NPC but the narrator omitted them from ``npcs_present``.

    ``source`` is ``"npcs"`` (stateful Npc match) or ``"npc_pool"``
    (pool member match) — when both stores hold the same name, ``"npcs"``
    wins (parallel to the npcs-shadows-pool rule in
    ``_apply_npc_mentions``). ``last_seen_turn`` propagates from the
    matched Npc's ``last_seen_turn`` (0 for pool-only matches).
    """
    attributes: dict[str, Any] = {
        "npc_name": npc_name,
        "source": source,
        "turn_number": turn_number,
        "last_seen_turn": last_seen_turn,
        "severity": "warning",
        **attrs,
    }
    with Span.open(
        SPAN_NPC_RECURRING_PRESENCE_MISSED,
        attributes,
        tracer_override=_tracer,
    ) as span:
        yield span
