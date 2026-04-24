"""Story 42-4 AC3 — OTEL span name parity against Rust source.

This suite asserts that every observable event emitted by the Rust mirror
(github.com/slabgorb/sidequest-api) under component ``combat`` or ``encounter``
is represented by a Python ``SPAN_*`` constant, OR carries a documented
deviation in ``tests/fixtures/telemetry/rust_watcher_event_catalog.json``.

The fixture is a frozen snapshot. The update procedure is documented in the
fixture's ``_meta.update_procedure`` field. Add a new Rust emit site? Update
the fixture and add the Python constant or a deviation rationale in the same
PR.

Why this suite is RED on introduction:
  Cross-referencing the Rust mirror surfaced ~19 encounter.* events that
  have no Python equivalent. Some are deliberate scope deferrals (Phase 4
  escalation, gold economy); others (encounter.state.resolved_by_trope,
  encounter.state.escalated) are load-bearing for GM-panel query patterns.
  Dev + Architect decide each case in green/spec-check.
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


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog() -> dict:
    """Load the frozen Rust watcher catalog."""
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def python_span_constants() -> dict[str, str]:
    """Map ``SPAN_COMBAT_*`` and ``SPAN_ENCOUNTER_*`` name → value from spans.py."""
    from sidequest.telemetry import spans as spans_mod

    return {
        name: getattr(spans_mod, name)
        for name in dir(spans_mod)
        if name.startswith(("SPAN_COMBAT_", "SPAN_ENCOUNTER_"))
        and isinstance(getattr(spans_mod, name), str)
    }


# ---------------------------------------------------------------------------
# Meta / catalog hygiene
# ---------------------------------------------------------------------------


def test_fixture_has_rust_source_metadata(catalog: dict) -> None:
    meta = catalog.get("_meta", {})
    assert meta.get("rust_source", "").startswith("https://github.com/slabgorb/"), (
        "Fixture must cite the Rust mirror URL so future TEAs can re-extract."
    )
    assert meta.get("snapshot_date"), (
        "Fixture must carry a snapshot_date — stale catalogs mask newly added "
        "Rust events."
    )


def test_catalog_entries_are_well_formed(catalog: dict) -> None:
    for entry in catalog["rust_events"]:
        assert entry["component"] in {"combat", "encounter"}, entry
        assert entry["field_name"] in {"event", "action"}, entry
        assert entry["value"], entry
        assert "source_path" in entry, entry
        # Either the entry maps to a Python span, or it explains the deviation.
        if entry.get("python_span") is None:
            assert entry.get("python_deviation"), (
                f"Entry {entry['value']!r} has no python_span and no "
                "python_deviation rationale — that's undocumented drift."
            )


# ---------------------------------------------------------------------------
# AC3: Rust → Python direction
# ---------------------------------------------------------------------------


def test_every_rust_event_is_mapped_or_deviated(
    catalog: dict, python_span_constants: dict[str, str]
) -> None:
    """Every Rust-source event must map to a Python span OR carry a deviation.

    A Python-side mapping is valid when the referenced SPAN_* constant exists
    AND its value matches the fixture-declared ``python_value``. A deviation
    is valid when ``python_deviation`` is non-empty prose.
    """
    missing: list[str] = []
    value_mismatches: list[tuple[str, str, str]] = []
    undocumented: list[str] = []

    for entry in catalog["rust_events"]:
        rust_value = entry["value"]
        py_span = entry.get("python_span")
        py_value = entry.get("python_value")
        deviation = entry.get("python_deviation", "").strip()

        if py_span is None:
            if not deviation:
                undocumented.append(rust_value)
            continue

        if py_span not in python_span_constants:
            missing.append(f"{rust_value} → {py_span} (constant not defined)")
            continue

        actual_value = python_span_constants[py_span]
        if py_value is not None and actual_value != py_value:
            value_mismatches.append((rust_value, py_span, actual_value))

    errors: list[str] = []
    if missing:
        errors.append(
            "Rust events reference Python SPAN_* constants that don't exist:\n  - "
            + "\n  - ".join(missing)
        )
    if value_mismatches:
        errors.append(
            "Python SPAN_* values drifted from fixture-declared python_value:\n  - "
            + "\n  - ".join(
                f"{rv}: fixture says python SPAN={ps} value={python_span_constants[ps]!r}"
                for rv, ps, _ in value_mismatches
            )
        )
    if undocumented:
        errors.append(
            "Rust events without Python mapping AND without deviation rationale "
            "(undocumented drift):\n  - " + "\n  - ".join(undocumented)
        )
    assert not errors, "\n\n".join(errors)


def test_blocking_gaps_flagged_as_ac3_required(catalog: dict) -> None:
    """Entries marked ``AC3 REQUIRES`` must resolve before story close.

    This test is the gate that flips the Story-42-4 RED state into GREEN:
    once Dev/Architect resolve every ``AC3 REQUIRES`` marker (either by
    adding the Python constant or downgrading the deviation text to a
    scope-deferral rationale), this test passes.
    """
    unresolved = [
        entry["value"]
        for entry in catalog["rust_events"]
        if "AC3 REQUIRES" in entry.get("python_deviation", "")
    ]
    assert not unresolved, (
        "These Rust events are still flagged as AC3-blocking. Either add the "
        "corresponding Python SPAN_* constant, wire the emit site, and update "
        "the fixture, or change the deviation rationale to document scope "
        "deferral (e.g., 'Phase 4 escalation, out of 42-4 scope'):\n  - "
        + "\n  - ".join(unresolved)
    )


# ---------------------------------------------------------------------------
# AC3: Python → Rust direction (forward-only drift is still drift)
# ---------------------------------------------------------------------------


def test_every_python_combat_encounter_span_is_accounted_for(
    catalog: dict, python_span_constants: dict[str, str]
) -> None:
    """Every Python SPAN_COMBAT_*/SPAN_ENCOUNTER_* constant must appear in the
    fixture — either mapped to a Rust event or in ``python_only_spans`` with
    a rationale.
    """
    mapped_to_rust = {
        entry["python_span"]
        for entry in catalog["rust_events"]
        if entry.get("python_span")
    }
    python_only = {
        entry["python_span"] for entry in catalog.get("python_only_spans", [])
    }
    known = mapped_to_rust | python_only

    orphans = sorted(set(python_span_constants) - known)
    assert not orphans, (
        "Python defines SPAN_COMBAT_*/SPAN_ENCOUNTER_* constants that are "
        "neither mapped to a Rust event in ``rust_events`` nor declared in "
        "``python_only_spans`` with a rationale:\n  - " + "\n  - ".join(orphans)
    )


def test_python_only_spans_carry_rationale(catalog: dict) -> None:
    for entry in catalog.get("python_only_spans", []):
        assert entry.get("rust_deviation"), (
            f"python_only_span {entry.get('python_span')!r} is missing "
            "rust_deviation rationale — Python-invented spans must explain why."
        )


# ---------------------------------------------------------------------------
# Wiring: the three Phase 3 domain events that must emit from production code
# ---------------------------------------------------------------------------


def test_phase_3_core_spans_are_imported_from_spans_module() -> None:
    """The three load-bearing Phase 3 spans must be importable from the module.

    This is a wiring test — it catches deletions or accidental renames that
    would break GM-panel consumers.
    """
    from sidequest.telemetry.spans import (
        SPAN_ENCOUNTER_BEAT_APPLIED,
        SPAN_ENCOUNTER_PHASE_TRANSITION,
        SPAN_ENCOUNTER_RESOLVED,
    )

    assert SPAN_ENCOUNTER_BEAT_APPLIED == "encounter.beat_applied"
    assert SPAN_ENCOUNTER_PHASE_TRANSITION == "encounter.phase_transition"
    assert SPAN_ENCOUNTER_RESOLVED == "encounter.resolved"
