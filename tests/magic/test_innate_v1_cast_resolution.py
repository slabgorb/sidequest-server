"""Story 47-10 AC5 + AC6 — innate_v1 cast resolution branch + OTEL span.

The cast_spell beat (defined in caverns_and_claudes/rules.yaml) currently
drains a spell_slots ledger bar via beat.resource_deltas in narration_apply.
This story adds the spell-catalog-driven RESOLUTION layer that runs after
the slot drain:

  1. Look up the cast spell in WorldMagicConfig.spell_catalogs[tradition]
  2. Branch on save.stat:
     - None  -> auto-apply effect_template (no opposed check)
     - !None -> route to opposed_check resolver with save.stat / save.effect
  3. Emit innate_v1.cast OTEL span with (actor_id, spell_id,
     validator_outcome, slot_consumed, save_skipped, save_stat?, save_result?,
     damage_applied?)

The exact integration seam is at the Dev's discretion. These tests probe the
public contract via a single resolution function. If the Dev places the
resolution in narration_apply.py inline, they can wrap it in a small helper
function with this signature for the test surface.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sidequest.magic.spell_catalog import Spell, SpellComponents, SpellSave


@pytest.fixture
def otel_capture() -> Iterator:
    """In-memory OTEL exporter for span assertions. Matches the pattern in
    tests/agents/conftest.py."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

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


def _spell(spell_id, save_stat, save_effect, effect_template="apply effect"):
    return Spell(
        id=spell_id,
        name=spell_id.replace("_", " ").title(),
        level=1,
        tradition="arcane",
        range="near",
        target="single",
        duration="instant",
        save=SpellSave(stat=save_stat, effect=save_effect),
        effect_template=effect_template,
        components=SpellComponents(verbal=True, somatic=True, material=None),
        backlash=None,
        narrator_register="prose",
        hard_limits_check=[],
        domain="physical",
        otel_attrs=["cast_intent"],
    )


# ---------------------------------------------------------------------------
# AC5 — null-stat auto-apply branch
# ---------------------------------------------------------------------------


def test_resolve_innate_cast_null_stat_skips_save():
    """Magic Missile shape — save.stat=None, effect=none. Resolution must
    skip the opposed-check pipeline and apply the effect template directly."""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast

    spell = _spell(
        "magic_missile",
        save_stat=None,
        save_effect="none",
        effect_template="1 momentum damage, auto-hit",
    )
    result = resolve_innate_v1_cast(
        spell=spell,
        actor_id="Rux",
        target_id="orc_1",
    )
    assert result.save_skipped is True, (
        "Null-stat spell must set save_skipped=True (auto-apply branch)"
    )
    assert result.validator_outcome == "ok"
    assert result.effect_applied == "1 momentum damage, auto-hit", (
        f"Auto-apply must apply the spell's effect_template; got {result.effect_applied!r}"
    )


def test_resolve_innate_cast_with_save_stat_runs_opposed_check():
    """Sleep shape — save.stat=WIS, effect=negates. Resolution must NOT
    auto-apply; it must emit a save request and branch the effect on result."""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast

    spell = _spell(
        "sleep", save_stat="WIS", save_effect="negates", effect_template="up to 4d4 HD unconscious"
    )
    result = resolve_innate_v1_cast(
        spell=spell,
        actor_id="Rux",
        target_id="orc_1",
        # The Dev decides how to inject the save resolver — could be a
        # callable in the function signature, could be from state. The
        # test asserts the *outcome shape*, not the wiring path.
        save_resolver=lambda stat, target: "fail",  # defender fails save
    )
    assert result.save_skipped is False
    assert result.save_stat == "WIS"
    assert result.save_result == "fail"
    assert result.effect_applied == "up to 4d4 HD unconscious", (
        "On save fail, full effect_template applies"
    )


def test_resolve_innate_cast_save_success_applies_save_effect():
    """When defender succeeds the save and effect=negates, no effect
    applies. (Other effects: halves -> partial; partial:<text> -> per
    authored partial.)"""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast

    spell = _spell("sleep", save_stat="WIS", save_effect="negates")
    result = resolve_innate_v1_cast(
        spell=spell,
        actor_id="Rux",
        target_id="orc_1",
        save_resolver=lambda stat, target: "success",
    )
    assert result.save_skipped is False
    assert result.save_result == "success"
    assert result.effect_applied is None or result.effect_applied == "", (
        f"On save success vs negates effect, no effect should apply; got {result.effect_applied!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — innate_v1.cast OTEL span
# ---------------------------------------------------------------------------


def test_resolve_innate_cast_emits_innate_v1_cast_span_on_success(otel_capture):
    """Successful cast emits an innate_v1.cast span with required attrs."""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast

    spell = _spell(
        "magic_missile",
        save_stat=None,
        save_effect="none",
        effect_template="1 momentum damage, auto-hit",
    )
    resolve_innate_v1_cast(
        spell=spell,
        actor_id="Rux",
        target_id="orc_1",
        slot_consumed=True,
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "innate_v1.cast"]
    assert len(spans) == 1, (
        f"Expected exactly one innate_v1.cast span; got {len(spans)}. "
        f"All emitted spans: {[s.name for s in otel_capture.get_finished_spans()]}"
    )
    span = spans[0]
    attrs = dict(span.attributes)
    # Required attrs (per spec §6 OTEL table):
    assert attrs.get("actor_id") == "Rux"
    assert attrs.get("spell_id") == "magic_missile"
    assert attrs.get("validator_outcome") == "ok"
    assert attrs.get("slot_consumed") is True
    assert attrs.get("save_skipped") is True


def test_resolve_innate_cast_span_includes_save_fields_on_save_path(otel_capture):
    """When save_skipped=False, the span must include save_stat and save_result."""
    from sidequest.magic.innate_v1_cast import resolve_innate_v1_cast

    spell = _spell(
        "sleep", save_stat="WIS", save_effect="negates", effect_template="up to 4d4 HD unconscious"
    )
    resolve_innate_v1_cast(
        spell=spell,
        actor_id="Rux",
        target_id="orc_1",
        slot_consumed=True,
        save_resolver=lambda stat, target: "fail",
    )
    spans = [s for s in otel_capture.get_finished_spans() if s.name == "innate_v1.cast"]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs.get("save_skipped") is False
    assert attrs.get("save_stat") == "WIS"
    assert attrs.get("save_result") == "fail"
