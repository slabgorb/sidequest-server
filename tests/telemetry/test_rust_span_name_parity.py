"""Story 42-4 AC3 — OTEL span name parity against Rust source.

Asserts every ``combat``/``encounter`` event emitted by the Rust mirror
(github.com/slabgorb/sidequest-api) appears in exactly one of two fixture
lists: ``mapped`` (has a Python ``SPAN_*`` constant) or ``deferred`` (has
an explicit scope reason pointing at a follow-up). Undocumented drift
fails the parity test — there is no deviation escape hatch in the entry
prose.

Fixture: ``tests/fixtures/telemetry/rust_watcher_event_catalog.json``
Update procedure: see fixture ``_meta.update_procedure``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "telemetry"
    / "rust_watcher_event_catalog.json"
)


@pytest.fixture(scope="module")
def catalog() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def python_span_constants() -> dict[str, str]:
    from sidequest.telemetry import spans as spans_mod

    return {
        name: getattr(spans_mod, name)
        for name in dir(spans_mod)
        if name.startswith(("SPAN_COMBAT_", "SPAN_ENCOUNTER_"))
        and isinstance(getattr(spans_mod, name), str)
    }


def test_fixture_has_rust_source_metadata(catalog: dict) -> None:
    meta = catalog["_meta"]
    assert meta["rust_source"].startswith("https://github.com/slabgorb/")
    assert meta["snapshot_date"]


def test_mapped_entries_reference_existing_constants(
    catalog: dict, python_span_constants: dict[str, str]
) -> None:
    problems: list[str] = []
    for entry in catalog["mapped"]:
        span = entry["python_span"]
        if span not in python_span_constants:
            problems.append(f"{entry['rust_value']} → {span} (not defined)")
            continue
        actual = python_span_constants[span]
        if actual != entry["python_value"]:
            problems.append(
                f"{entry['rust_value']} → {span}: fixture says "
                f"{entry['python_value']!r}, actual {actual!r}"
            )
    assert not problems, "Mapped entries broken:\n  - " + "\n  - ".join(problems)


def test_deferred_entries_carry_scope_reason(catalog: dict) -> None:
    missing = [
        e["rust_value"]
        for e in catalog["deferred"]
        if not e.get("deferred_to", "").strip()
    ]
    assert not missing, (
        "Deferred entries without a ``deferred_to`` scope reason:\n  - "
        + "\n  - ".join(missing)
    )


def test_rust_events_appear_in_exactly_one_list(catalog: dict) -> None:
    mapped = {e["rust_value"] for e in catalog["mapped"]}
    deferred = {e["rust_value"] for e in catalog["deferred"]}
    overlap = mapped & deferred
    assert not overlap, (
        "Rust events appear in both mapped and deferred (pick one):\n  - "
        + "\n  - ".join(sorted(overlap))
    )


def test_every_python_combat_encounter_span_is_accounted_for(
    catalog: dict, python_span_constants: dict[str, str]
) -> None:
    mapped_spans = {e["python_span"] for e in catalog["mapped"]}
    python_only = {e["python_span"] for e in catalog["python_only_spans"]}
    known = mapped_spans | python_only
    orphans = sorted(set(python_span_constants) - known)
    assert not orphans, (
        "Python SPAN_COMBAT_*/SPAN_ENCOUNTER_* constants missing from the "
        "catalog (add to ``mapped`` with a Rust rust_value, or to "
        "``python_only_spans`` with a reason):\n  - " + "\n  - ".join(orphans)
    )


def test_python_only_spans_carry_reason(catalog: dict) -> None:
    for entry in catalog["python_only_spans"]:
        assert entry.get("reason", "").strip(), (
            f"python_only_span {entry.get('python_span')!r} missing reason."
        )


def test_phase_3_core_spans_are_importable() -> None:
    from sidequest.telemetry.spans import (
        SPAN_ENCOUNTER_BEAT_APPLIED,
        SPAN_ENCOUNTER_PHASE_TRANSITION,
        SPAN_ENCOUNTER_RESOLVED,
        SPAN_ENCOUNTER_RESOLVED_BY_TROPE,
    )

    assert SPAN_ENCOUNTER_BEAT_APPLIED == "encounter.beat_applied"
    assert SPAN_ENCOUNTER_PHASE_TRANSITION == "encounter.phase_transition"
    assert SPAN_ENCOUNTER_RESOLVED == "encounter.resolved"
    assert SPAN_ENCOUNTER_RESOLVED_BY_TROPE == "encounter.resolved_by_trope"
