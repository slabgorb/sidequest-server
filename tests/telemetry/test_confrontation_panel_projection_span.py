"""Panel-projection beat_filter span — source-attribute discrimination.

Story 49-7 RED. Story AC:

    OTEL: confrontation_beat_filter_span already exists in the
    narrator-prompt call site. Add a SECOND emission site at the panel-
    projection call, distinguishable via source='ui_panel_projection'
    vs the existing source='narrator_prompt'. Sebastien's GM panel
    needs to see when the panel filter runs separately from the prompt
    filter.

Two distinct emission sites would otherwise look identical in the
watcher dashboard — same span name, same actor/class attributes,
same available_beat_ids. Without the ``source`` tag a regression that
silently drops the panel-projection emit and falls back to the
narrator-prompt one is invisible to the GM panel.

The narrator-prompt-side emit at ``sidequest.agents.narrator.py:364``
already exists; this test asserts the source attribute is added to its
``span_kwargs`` dict as part of the same change. The panel-projection
emit is new and lives inside
``build_confrontation_payload(recipient_pc=...)``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import (
    BeatDef,
    BeatKind,
    ConfrontationDef,
    MetricDef,
)
from sidequest.server.dispatch.confrontation import build_confrontation_payload
from sidequest.telemetry.spans.encounter import SPAN_CONFRONTATION_BEAT_FILTER

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def otel_capture():
    """In-memory span exporter attached to the live TracerProvider."""
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


def _cdef_two_beats() -> ConfrontationDef:
    return ConfrontationDef(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", starting=0, threshold=10),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=10),
        beats=[
            BeatDef(id="attack", label="Attack", kind=BeatKind.strike, stat_check="STR"),
            BeatDef(
                id="backstab",
                label="Backstab",
                kind=BeatKind.strike,
                stat_check="DEX",
                class_filter=["Thief"],
            ),
        ],
    )


def _fighter() -> ClassDef:
    return ClassDef(
        id="fighter",
        display_name="Fighter",
        rpg_role="tank",
        jungian_default="warrior",
        prime_requisite="STR",
        minimum_score=9,
        kit_table="fighter_kit",
        flavor="-",
        encounter_beat_choices=["attack"],
    )


def _encounter() -> StructuredEncounter:
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=[EncounterActor(name="Carl", role="combatant", side="player")],
    )


# ---------------------------------------------------------------------------
# Runtime checks — drive the panel-projection path and inspect spans.
# ---------------------------------------------------------------------------


def test_panel_projection_emits_beat_filter_span_with_source_ui_panel_projection(
    otel_capture: InMemorySpanExporter,
) -> None:
    """build_confrontation_payload(recipient_pc=...) MUST emit a
    confrontation_beat_filter_span tagged source='ui_panel_projection'.
    The GM panel filters watcher events by this attribute to render the
    panel-side filter trace separately from the prompt-side one.
    """
    build_confrontation_payload(
        encounter=_encounter(),
        cdef=_cdef_two_beats(),
        genre_slug="caverns_and_claudes",
        recipient_pc=(_fighter(), 0.0, None),
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == SPAN_CONFRONTATION_BEAT_FILTER]
    panel_spans = [s for s in spans if s.attributes.get("source") == "ui_panel_projection"]
    assert len(panel_spans) >= 1, (
        f"build_confrontation_payload(recipient_pc=...) must emit a "
        f"beat_filter span with source='ui_panel_projection'. Got spans: "
        f"{[(s.name, dict(s.attributes)) for s in spans]!r}"
    )


def test_panel_projection_span_carries_required_attributes_for_gm_dashboard(
    otel_capture: InMemorySpanExporter,
) -> None:
    """The route extractor at ``telemetry/spans/encounter.py:beat_filter
    route.extract`` reads class_name, confrontation_type, pool_size,
    filtered_size, available_beat_ids. The panel-projection emit must
    carry all of them so the GM panel doesn't render half-empty rows
    after a regression."""
    build_confrontation_payload(
        encounter=_encounter(),
        cdef=_cdef_two_beats(),
        genre_slug="caverns_and_claudes",
        recipient_pc=(_fighter(), 0.0, None),
    )
    panel_spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == SPAN_CONFRONTATION_BEAT_FILTER
        and s.attributes.get("source") == "ui_panel_projection"
    ]
    assert panel_spans, "panel-projection span missing (covered by sibling test, but required here too)"
    attrs = dict(panel_spans[0].attributes)
    assert attrs.get("class_name") == "Fighter", (
        f"missing/wrong class_name on panel-projection span; attrs={attrs!r}"
    )
    assert attrs.get("confrontation_type") == "combat"
    assert "pool_size" in attrs, f"missing pool_size; attrs={attrs!r}"
    assert "filtered_size" in attrs, f"missing filtered_size; attrs={attrs!r}"
    assert attrs.get("pool_size") == 2
    # Fighter sees only 'attack' (Thief-filtered 'backstab' excluded).
    assert attrs.get("filtered_size") == 1


def test_no_panel_projection_span_when_recipient_pc_omitted(
    otel_capture: InMemorySpanExporter,
) -> None:
    """The unfiltered (backward-compat) path must NOT emit a panel-
    projection span — there was no per-PC filter decision to record.
    Without this guard a regression where Dev always emits the span
    (even on the legacy path) would double-count the GM panel rows.
    """
    build_confrontation_payload(
        encounter=_encounter(),
        cdef=_cdef_two_beats(),
        genre_slug="caverns_and_claudes",
    )
    panel_spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == SPAN_CONFRONTATION_BEAT_FILTER
        and s.attributes.get("source") == "ui_panel_projection"
    ]
    assert panel_spans == [], (
        f"unfiltered call path must not emit a panel-projection span; "
        f"got {[dict(s.attributes) for s in panel_spans]!r}"
    )


def test_panel_projection_span_includes_cast_spell_rejection_reason_when_applicable(
    otel_capture: InMemorySpanExporter,
) -> None:
    """Symmetry with the narrator-prompt site: when cast_spell is in the
    pool and the recipient's class is in cast_spell's filter but the
    spell-slot or prepared-list gate rejects, the span carries the
    rejection reason. Sebastien's GM panel uses this to tell 'Mage out
    of slots' from 'Mage didn't memorize anything'.
    """
    mage = ClassDef(
        id="mage",
        display_name="Mage",
        rpg_role="caster",
        jungian_default="sage",
        prime_requisite="INT",
        minimum_score=9,
        kit_table="mage_kit",
        flavor="-",
        encounter_beat_choices=["attack", "cast_spell"],
    )
    cdef = ConfrontationDef(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", starting=0, threshold=10),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=10),
        beats=[
            BeatDef(id="attack", label="Attack", kind=BeatKind.strike, stat_check="STR"),
            BeatDef(
                id="cast_spell",
                label="Cast Spell",
                kind=BeatKind.strike,
                stat_check="INT",
                class_filter=["Mage"],
            ),
        ],
    )

    # Slot > 0 but nothing prepared → 'unprepared' rejection reason.
    build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(mage, 2.0, {}),
    )

    panel_spans = [
        s
        for s in otel_capture.get_finished_spans()
        if s.name == SPAN_CONFRONTATION_BEAT_FILTER
        and s.attributes.get("source") == "ui_panel_projection"
    ]
    assert panel_spans, "panel-projection span missing"
    reasons = {s.attributes.get("cast_spell_rejection_reason") for s in panel_spans}
    assert "unprepared" in reasons, (
        f"panel-projection span must surface cast_spell_rejection_reason='unprepared' "
        f"when mage has slots but no prep; got reasons={reasons!r}"
    )


# ---------------------------------------------------------------------------
# Static checks — narrator.py existing emit must add source='narrator_prompt'.
# ---------------------------------------------------------------------------


def test_narrator_prompt_site_passes_source_narrator_prompt_to_span() -> None:
    """The existing emit at narrator.py:364 is wrapped around
    confrontation_beat_filter_span(**span_kwargs). After this story
    span_kwargs must include source='narrator_prompt' so the new panel
    emit (source='ui_panel_projection') is distinguishable. Static
    check on the source file — Dev's edit is a one-line addition to the
    dict construction at narrator.py:353.
    """
    src = (_REPO_ROOT / "sidequest/agents/narrator.py").read_text(encoding="utf-8")
    # The literal must appear inside the span_kwargs dict construction
    # (a regex covers both 'narrator_prompt' and "narrator_prompt").
    pattern = re.compile(r"['\"]source['\"]\s*:\s*['\"]narrator_prompt['\"]")
    assert pattern.search(src) is not None, (
        "narrator.py must tag its confrontation_beat_filter_span emit with "
        "source='narrator_prompt' (look for span_kwargs['source'] = "
        "'narrator_prompt' or equivalent dict literal at the narrator-prompt "
        "filter site)."
    )


def test_narrator_prompt_source_literal_lives_near_existing_filter_emit() -> None:
    """Localize the static check: the source='narrator_prompt' literal
    must appear in the same function/block as the existing
    confrontation_beat_filter_span call. Dev could otherwise add the
    string elsewhere and trivially pass test_narrator_prompt_site_*
    above without actually wiring the attribute onto the span.
    """
    src_path = _REPO_ROOT / "sidequest/agents/narrator.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))

    target_call_line: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            (isinstance(func, ast.Name) and func.id == "confrontation_beat_filter_span")
            or (
                isinstance(func, ast.Attribute)
                and func.attr == "confrontation_beat_filter_span"
            )
        ):
            target_call_line = node.lineno
            break
    assert target_call_line is not None, (
        "narrator.py no longer calls confrontation_beat_filter_span — the "
        "static-check target is gone. Re-anchor this test on the new call site."
    )

    # Allow source literal within ±60 lines of the span call (covers the
    # span_kwargs construction block above and a generous after-margin).
    lines = src_path.read_text(encoding="utf-8").splitlines()
    near_window = lines[max(0, target_call_line - 60) : target_call_line + 5]
    pattern = re.compile(r"['\"]source['\"]\s*:\s*['\"]narrator_prompt['\"]")
    assert any(pattern.search(line) for line in near_window), (
        f"source='narrator_prompt' literal must appear within ~60 lines of the "
        f"confrontation_beat_filter_span call at narrator.py:{target_call_line}; "
        f"current window:\n" + "\n".join(near_window)
    )
