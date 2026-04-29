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
        SPAN_NPC_PC_NAME_SKIPPED, attributes, tracer_override=_tracer,
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
        SPAN_NPC_REGISTRY_HP_SET, attributes, tracer_override=_tracer,
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
