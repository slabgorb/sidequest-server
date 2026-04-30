"""Story 45-20 — trope-resolution-handshake span constant + routing.

The chapter-promotion path mutates ``active_tropes[*].status`` to
``"resolved"`` but no downstream consumer wires the durable record (the
``quest_log`` entry / ``active_stakes`` marker the next turn's narrator
reads). Story 45-20 adds the missing handshake. Without an entry in
``SPAN_ROUTES`` for the handshake span, the GM panel's typed
state_transition feed cannot tell whether the handshake fired — exactly
the lie-detector blind spot Sebastien (mechanical-first player) needs
to see.

The pre-existing ``SPAN_TROPE_RESOLVE`` constant has been
flat-only since the port and never reached the typed feed; this story
also promotes it.

These tests pin both routing decisions in place so a future rename or
reshape trips a hard test failure rather than a silent dashboard gap.
"""

from __future__ import annotations

import pytest


def test_handshake_span_constant_value() -> None:
    """The handshake constant must match the canonical span name used by
    the GM panel watcher rules. Renaming the constant without renaming
    the canonical name silently breaks the dashboard filter.
    """

    from sidequest.telemetry.spans import SPAN_TROPE_RESOLUTION_HANDSHAKE

    assert SPAN_TROPE_RESOLUTION_HANDSHAKE == "trope.resolution_handshake"


def test_handshake_span_is_routed_as_state_transition() -> None:
    """The handshake span belongs on the state_transition typed feed —
    it documents a snapshot mutation (quest_log/active_stakes) tied to a
    trope status flip into ``"resolved"``. FLAT_ONLY_SPANS would route
    only to agent_span_close and the panel's typed Subsystems tab would
    silently miss the handshake.
    """

    from sidequest.telemetry.spans import (
        SPAN_ROUTES,
        SPAN_TROPE_RESOLUTION_HANDSHAKE,
    )

    assert SPAN_TROPE_RESOLUTION_HANDSHAKE in SPAN_ROUTES, (
        "trope.resolution_handshake must be registered in SPAN_ROUTES; "
        "FLAT_ONLY_SPANS routing alone is invisible to the typed Subsystems tab."
    )
    route = SPAN_ROUTES[SPAN_TROPE_RESOLUTION_HANDSHAKE]
    assert route.event_type == "state_transition", (
        "trope.resolution_handshake must be a state_transition event "
        "(it mutates quest_log/active_stakes), not a flat span."
    )
    assert route.component, (
        "trope.resolution_handshake route must declare a component name "
        "for GM panel grouping."
    )


def test_handshake_span_extract_pulls_lie_detector_attributes() -> None:
    """Sebastien's mechanical-visibility AC: every handshake span must
    surface the load-bearing fields — trope_id, prior_status, new_status,
    quest_log_key, active_stakes_appended, source — so the GM panel
    watcher can render the lie-detector row without re-reading the raw
    span body. ``new_status`` is always ``"resolved"`` by construction
    but is included so the panel's table column shows it explicitly.
    """

    from sidequest.telemetry.spans import (
        SPAN_ROUTES,
        SPAN_TROPE_RESOLUTION_HANDSHAKE,
    )

    route = SPAN_ROUTES[SPAN_TROPE_RESOLUTION_HANDSHAKE]

    class _FakeSpan:
        name = "trope.resolution_handshake"
        attributes = {
            "trope_id": "extraction_panic",
            "prior_status": "progressing",
            "new_status": "resolved",
            "interaction": 17,
            "quest_log_key": "trope_extraction_panic",
            "active_stakes_appended": True,
            "source": "chapter_promotion",
        }

    fields = route.extract(_FakeSpan())
    for required in (
        "trope_id",
        "prior_status",
        "new_status",
        "interaction",
        "quest_log_key",
        "active_stakes_appended",
        "source",
    ):
        assert required in fields, (
            "trope.resolution_handshake route extract() missing "
            f"{required!r}; got {sorted(fields)}. Without this field "
            "the GM panel cannot render the lie-detector row."
        )


def test_handshake_span_not_in_flat_only() -> None:
    """Belt-and-braces — the handshake constant must not be in
    FLAT_ONLY_SPANS. Routed and flat-only are mutually exclusive; the
    typed feed silently drops anything in the flat set.
    """

    from sidequest.telemetry.spans import (
        FLAT_ONLY_SPANS,
        SPAN_TROPE_RESOLUTION_HANDSHAKE,
    )

    assert SPAN_TROPE_RESOLUTION_HANDSHAKE not in FLAT_ONLY_SPANS


# ---------------------------------------------------------------------------
# Existing SPAN_TROPE_RESOLVE — promote out of FLAT_ONLY_SPANS.
# ---------------------------------------------------------------------------


def test_span_trope_resolve_constant_unchanged() -> None:
    """Sanity — the existing constant value is canonical and may not
    rename during the promotion. Downstream watcher rules and any
    Jaeger/OTLP collector key on the literal ``"trope_resolve"``.
    """

    from sidequest.telemetry.spans import SPAN_TROPE_RESOLVE

    assert SPAN_TROPE_RESOLVE == "trope_resolve"


def test_span_trope_resolve_promoted_out_of_flat_only() -> None:
    """SPAN_TROPE_RESOLVE was port-flat-only and never surfaced on the
    typed Subsystems feed. The 45-20 promotion moves it to SPAN_ROUTES so
    the existing emit sites become visible alongside the new handshake.
    """

    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_TROPE_RESOLVE

    assert SPAN_TROPE_RESOLVE not in FLAT_ONLY_SPANS, (
        "SPAN_TROPE_RESOLVE must be removed from FLAT_ONLY_SPANS as part of "
        "the 45-20 promotion; otherwise the typed feed continues to drop it."
    )


def test_span_trope_resolve_routed_as_state_transition() -> None:
    """After promotion, ``trope_resolve`` is a state_transition event.
    Component grouping is left to the implementation but must be set.
    """

    from sidequest.telemetry.spans import SPAN_ROUTES, SPAN_TROPE_RESOLVE

    assert SPAN_TROPE_RESOLVE in SPAN_ROUTES, (
        "SPAN_TROPE_RESOLVE must be registered in SPAN_ROUTES after the "
        "45-20 promotion; flat-only routing keeps it off the typed feed."
    )
    route = SPAN_ROUTES[SPAN_TROPE_RESOLVE]
    assert route.event_type == "state_transition"
    assert route.component, (
        "trope_resolve route must declare a component for panel grouping."
    )


# ---------------------------------------------------------------------------
# Routing-completeness regression — every routed constant exported by the
# package must be either in SPAN_ROUTES or FLAT_ONLY_SPANS.
# ---------------------------------------------------------------------------


def test_handshake_span_does_not_break_routing_completeness() -> None:
    """The package-level routing-completeness invariant (every span
    constant must be either routed or flat-only, never both, never
    neither) must still hold after the new constant is added. Failing
    this check usually means a registration was forgotten in
    ``spans/trope.py``.
    """

    from sidequest.telemetry import spans as _spans

    constants = [
        name for name in dir(_spans)
        if name.startswith("SPAN_") and name.isupper()
    ]
    routed = set(_spans.SPAN_ROUTES)
    flat = set(_spans.FLAT_ONLY_SPANS)

    missing = []
    overlap = []
    for name in constants:
        value = getattr(_spans, name)
        if not isinstance(value, str):
            continue
        if value in routed and value in flat:
            overlap.append((name, value))
        if value not in routed and value not in flat:
            missing.append((name, value))

    assert not overlap, (
        f"span constants in BOTH SPAN_ROUTES and FLAT_ONLY_SPANS: {overlap}"
    )
    # Pre-existing missing entries are tracked elsewhere; assert that the
    # newly added handshake constant in particular is registered.
    from sidequest.telemetry.spans import SPAN_TROPE_RESOLUTION_HANDSHAKE

    handshake_missing = [
        (n, v) for n, v in missing if v == SPAN_TROPE_RESOLUTION_HANDSHAKE
    ]
    assert not handshake_missing, (
        "trope.resolution_handshake constant exists but is neither in "
        "SPAN_ROUTES nor in FLAT_ONLY_SPANS — registration was forgotten."
    )


# ---------------------------------------------------------------------------
# Helper context manager — must exist and emit a span with the canonical
# attribute set. The emit-site signature is what the handshake call site
# imports so a renamed/missing helper trips a hard import failure rather
# than a silent missed-emit.
# ---------------------------------------------------------------------------


def test_trope_resolution_handshake_span_helper_emits_named_span() -> None:
    """The ``trope_resolution_handshake_span`` context manager must emit
    a span whose name matches ``SPAN_TROPE_RESOLUTION_HANDSHAKE`` and
    whose attributes include the lie-detector payload.
    """

    pytest.importorskip("opentelemetry")
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from sidequest.telemetry.setup import init_tracer
    from sidequest.telemetry.spans import (
        SPAN_TROPE_RESOLUTION_HANDSHAKE,
        trope_resolution_handshake_span,
    )

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)

    try:
        with trope_resolution_handshake_span(
            trope_id="extraction_panic",
            prior_status="progressing",
            new_status="resolved",
            interaction=17,
            quest_log_key="trope_extraction_panic",
            active_stakes_appended=True,
            source="chapter_promotion",
        ):
            pass

        spans = [
            s for s in exporter.get_finished_spans()
            if s.name == SPAN_TROPE_RESOLUTION_HANDSHAKE
        ]
        assert len(spans) == 1, (
            "trope_resolution_handshake_span must emit exactly one span; "
            f"got {[s.name for s in exporter.get_finished_spans()]}"
        )
        attrs = dict(spans[0].attributes or {})
        assert attrs.get("trope_id") == "extraction_panic"
        assert attrs.get("prior_status") == "progressing"
        assert attrs.get("new_status") == "resolved"
        assert attrs.get("interaction") == 17
        assert attrs.get("quest_log_key") == "trope_extraction_panic"
        assert attrs.get("active_stakes_appended") is True
        assert attrs.get("source") == "chapter_promotion"
    finally:
        processor.shutdown()
        exporter.clear()
