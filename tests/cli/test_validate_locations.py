"""TDD tests for ``pf validate locations`` core (Story 54-3 / ADR-109).

Covers all seven ACs from sprint/context/context-story-54-3.md:
  AC-1: CLI scans every wired world in a pack; exit 1 on hard error else 0.
  AC-2: Well-formedness — duplicate id, blank label, blank id, extra fields,
        binding on tier=flavor_only, real_object missing binding.
  AC-3: Binding resolution — npc/clue/scenario_clue refs resolve;
        location_feature is free-form.
  AC-4: Prose-manifest coherence — warning-only (never blocks exit code);
        per-pack ``generic_allowlist`` silences; known NPCs do not warn.
  AC-5: Programmatic entry ``validate_locations_in_world(world_dir)``
        returns a ``ValidationResult`` with ``.errors`` and ``.warnings``.
        This is the surface Story 55-1's post-materialize test consumes.
  AC-6: Validator runs against every wired pack (smoke; just confirms
        the multi-pack entry point exists and produces zero hard errors
        on the checked-in fixture pack ``wf_ok``).
  AC-7: Wiring — ``python -m sidequest.cli.validate locations --help`` is
        registered + invokable.

Test paranoia: every fixture lives under
``tests/fixtures/validate_locations/<case>/`` and shapes itself so that
exactly one diagnostic should fire (or none, for the ok cases). When
multiple checks could overlap, the test asserts on the SPECIFIC issue
code, not just on count, so accidental other failures surface as
separate test failures rather than blurring together.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sidequest.cli.validate.locations import (
    Issue,
    ValidationResult,
    validate_locations_in_world,
    validate_packs,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "validate_locations"
SERVER_ROOT = Path(__file__).resolve().parents[2]


def _pack_result(pack: str) -> ValidationResult:
    """Run the multi-pack entry against one fixture pack (single-pack root)."""
    return validate_packs([FIXTURES / pack])


def _world_result(pack: str, world: str = "sample") -> ValidationResult:
    """Run the per-world programmatic entry — the AC-5 / Story 55-1 surface."""
    return validate_locations_in_world(FIXTURES / pack / "worlds" / world)


# ---------------------------------------------------------------------------
# AC-2: Well-formedness
# ---------------------------------------------------------------------------


def test_well_formed_pack_has_no_errors() -> None:
    """Baseline: a well-formed fixture produces zero hard errors. Prose-drift
    warnings may fire — that's AC-4 territory and not asserted here."""
    res = _pack_result("wf_ok")
    assert res.errors == [], f"unexpected errors: {[i.code for i in res.errors]}"


def test_duplicate_entity_id_within_region_errors() -> None:
    """AC-2: two entities sharing an id in the same region → DUPLICATE_ENTITY_ID."""
    res = _pack_result("wf_duplicate_id")
    dupes = [i for i in res.errors if i.code == "DUPLICATE_ENTITY_ID"]
    assert len(dupes) == 1, f"expected exactly one duplicate; got {[i.code for i in res.errors]}"
    assert "well" in dupes[0].message
    assert dupes[0].region_id == "village_square"
    assert dupes[0].severity == "error"


def test_real_object_without_binding_errors() -> None:
    """AC-2: tier=real_object MUST carry a binding."""
    res = _pack_result("wf_real_object_no_binding")
    bad = [i for i in res.errors if i.code == "REAL_OBJECT_REQUIRES_BINDING"]
    assert len(bad) == 1
    assert "orphan_chest" in bad[0].message


def test_flavor_only_with_binding_errors() -> None:
    """AC-2: ``binding on tier=flavor_only`` is explicitly disallowed.

    Pydantic permits ``binding: None`` on any tier, so this check is the
    validator's responsibility. The plan only covers real_object→binding;
    flavor_only→no-binding is the symmetric AC-2 clause.
    """
    res = _pack_result("wf_flavor_only_with_binding")
    bad = [i for i in res.errors if i.code in {"FLAVOR_ONLY_FORBIDS_BINDING", "MALFORMED_ENTITY"}]
    assert bad, f"expected a flavor-only-binding error, got {[i.code for i in res.errors]}"


def test_malformed_entities_report_each_independently() -> None:
    """AC-2: blank id, blank label, and unknown extra field each produce a
    distinct MALFORMED_ENTITY issue. They must not short-circuit each other."""
    res = _pack_result("wf_malformed")
    malformed = [i for i in res.errors if i.code == "MALFORMED_ENTITY"]
    # Three independently-bad entities in one region; validator must report
    # all three, not stop at the first.
    assert len(malformed) == 3, (
        f"expected 3 malformed issues, got {len(malformed)}: {[i.message for i in malformed]}"
    )


def test_malformed_entry_carries_source_file_path() -> None:
    """AC-2: every diagnostic must reference the source file (for CI grep + IDE jumps)."""
    res = _pack_result("wf_malformed")
    malformed = [i for i in res.errors if i.code == "MALFORMED_ENTITY"]
    assert malformed
    assert all("cartography.yaml" in i.file for i in malformed)
    assert all(i.pack == "wf_malformed" for i in malformed)


# ---------------------------------------------------------------------------
# AC-3: Binding resolution
# ---------------------------------------------------------------------------


def test_npc_binding_to_unknown_id_errors() -> None:
    """AC-3: npc binding ref must appear in the world's npcs.yaml."""
    res = _pack_result("binding_bad_npc")
    bad = [i for i in res.errors if i.code == "BINDING_UNRESOLVED"]
    assert len(bad) == 1
    assert "nonexistent_npc_id" in bad[0].message


def test_clue_binding_to_unknown_scenario_clue_errors() -> None:
    """AC-3: scenario_clue ref must appear in some scenarios/*.yaml clue list."""
    res = _pack_result("binding_bad_clue")
    bad = [i for i in res.errors if i.code == "BINDING_UNRESOLVED"]
    assert len(bad) == 1
    assert "nonexistent_clue_id" in bad[0].message


def test_location_feature_binding_is_free_form() -> None:
    """AC-3: location_feature is intentionally free-form — never resolved."""
    res = _pack_result("binding_location_feature_ok")
    unresolved = [i for i in res.errors if i.code == "BINDING_UNRESOLVED"]
    assert unresolved == []


def test_clean_pack_produces_no_binding_errors() -> None:
    """AC-3 negative: wf_ok's well + notice board bind to location_feature
    refs that the validator must not try to resolve."""
    res = _pack_result("wf_ok")
    unresolved = [i for i in res.errors if i.code == "BINDING_UNRESOLVED"]
    assert unresolved == []


# ---------------------------------------------------------------------------
# AC-4: Prose-manifest coherence (warning only, NEVER errors)
# ---------------------------------------------------------------------------


def test_unallowlisted_definite_noun_phrase_warns() -> None:
    """AC-4: 'the dragon' in prose, no dragon in manifest, no allowlist
    entry → at least one PROSE_DRIFT warning containing 'dragon'."""
    res = _pack_result("coherence_drift")
    drift = [i for i in res.warnings if i.code == "PROSE_DRIFT"]
    assert any("dragon" in i.message.lower() for i in drift), (
        f"expected a 'dragon' drift warning; got: {[i.message for i in drift]}"
    )


def test_prose_drift_never_promoted_to_error() -> None:
    """AC-4: warnings NEVER appear in .errors — exit code stays 0."""
    res = _pack_result("coherence_drift")
    assert all(i.code != "PROSE_DRIFT" for i in res.errors), (
        "PROSE_DRIFT must be warning-only, never an error"
    )


def test_allowlist_silences_generic_phrases() -> None:
    """AC-4: per-pack ``generic_allowlist`` suppresses the listed phrases.

    wf_ok lists 'the centre' / 'the rules' / 'the village' — none of which
    appear in entities[] — and the prose mentions them. Allowlist must
    prevent PROSE_DRIFT warnings on those tokens.
    """
    res = _pack_result("wf_ok")
    drift = [i for i in res.warnings if i.code == "PROSE_DRIFT"]
    drift_msgs = " ".join(i.message.lower() for i in drift)
    for silenced in ("the centre", "the rules"):
        assert silenced not in drift_msgs, (
            f"allowlist should have silenced {silenced!r}; got drift: {drift_msgs}"
        )


def test_known_npc_name_in_prose_does_not_warn() -> None:
    """AC-4: prose mentions 'Cassia', npcs.yaml lists her → no warning for Cassia.

    'the bar' resolves via the entity manifest (entity label 'the bar').
    The fixture is constructed so NOTHING in the prose should drift —
    asserts no PROSE_DRIFT warnings at all.
    """
    res = _pack_result("coherence_npc_resolved")
    drift = [i for i in res.warnings if i.code == "PROSE_DRIFT"]
    assert drift == [], (
        f"prose ('Cassia leans on the bar') should resolve cleanly, "
        f"got: {[i.message for i in drift]}"
    )


# ---------------------------------------------------------------------------
# Room-level (cookbook / per-room rooms/<id>.yaml) path
# ---------------------------------------------------------------------------


def test_per_room_yaml_entities_are_walked() -> None:
    """The materializer writes <world>/rooms/<id>.yaml with entities[].
    The validator must walk those just like cartography regions. The
    fixture has a duplicate id in rooms/cavern_001.yaml — must surface."""
    res = _pack_result("room_level")
    dupes = [i for i in res.errors if i.code == "DUPLICATE_ENTITY_ID"]
    assert len(dupes) == 1
    assert "stalactite" in dupes[0].message
    assert "cavern_001" in dupes[0].file or dupes[0].region_id == "cavern_001"


# ---------------------------------------------------------------------------
# AC-5: Programmatic entry — the Story 55-1 consumer surface
# ---------------------------------------------------------------------------


def test_per_world_entry_returns_validation_result_with_errors_and_warnings() -> None:
    """AC-5: ``validate_locations_in_world(world_dir)`` returns an object with
    ``.errors`` and ``.warnings`` lists. This is the EXACT signature
    Story 55-1's post-materialize test calls (test_pf_validate_locations_on_materialized.py)."""
    res = _world_result("wf_ok")
    assert isinstance(res, ValidationResult)
    assert isinstance(res.errors, list)
    assert isinstance(res.warnings, list)
    assert all(isinstance(i, Issue) for i in res.errors + res.warnings)


def test_per_world_entry_reports_hard_errors() -> None:
    """AC-5: the per-world entry surfaces hard errors, not just warnings."""
    res = _world_result("wf_duplicate_id")
    assert any(i.code == "DUPLICATE_ENTITY_ID" for i in res.errors)


def test_per_world_entry_accepts_world_with_no_cartography() -> None:
    """AC-5: a world directory with neither cartography.yaml nor rooms/
    must produce a clean empty result — not crash. This is the
    pre-materialization state of every procedural world."""
    res = validate_locations_in_world(FIXTURES / "wf_ok")  # not a world dir
    assert isinstance(res, ValidationResult)
    assert res.errors == [], (
        f"missing-cartography must be silent, got: {[i.code for i in res.errors]}"
    )


# ---------------------------------------------------------------------------
# AC-1: CLI exit codes
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sidequest.cli.validate", "locations", *args],
        capture_output=True,
        text=True,
        cwd=str(SERVER_ROOT),
    )


def test_cli_exits_zero_on_clean_pack() -> None:
    """AC-1: clean pack → exit 0."""
    result = _run_cli(
        "--genre-packs-root",
        str(FIXTURES / "wf_ok"),
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_exits_nonzero_on_hard_error() -> None:
    """AC-1: pack with hard error → exit 1."""
    result = _run_cli(
        "--genre-packs-root",
        str(FIXTURES / "wf_duplicate_id"),
    )
    assert result.returncode != 0, (
        f"expected nonzero exit, got 0\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_exits_zero_on_warning_only_pack() -> None:
    """AC-4: warnings NEVER block the exit code."""
    result = _run_cli(
        "--genre-packs-root",
        str(FIXTURES / "coherence_drift"),
    )
    assert result.returncode == 0, (
        f"warnings-only pack must exit 0; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_cli_json_output_is_machine_readable() -> None:
    """AC-1: --json emits a parseable structured report with the documented shape."""
    result = _run_cli(
        "--json",
        "--genre-packs-root",
        str(FIXTURES / "wf_duplicate_id"),
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert isinstance(payload["errors"], list)
    assert isinstance(payload["warnings"], list)
    assert any(e["code"] == "DUPLICATE_ENTITY_ID" for e in payload["errors"])


# ---------------------------------------------------------------------------
# AC-7: Wiring — subcommand registered under cli/validate
# ---------------------------------------------------------------------------


def test_cli_subcommand_help_runs() -> None:
    """AC-7: ``python -m sidequest.cli.validate locations --help`` returns 0.

    Catches the most common wiring failure: a new validator file exists
    but ``__main__.py`` never registered it.
    """
    result = subprocess.run(
        [sys.executable, "-m", "sidequest.cli.validate", "locations", "--help"],
        capture_output=True,
        text=True,
        cwd=str(SERVER_ROOT),
    )
    assert result.returncode == 0, f"locations subcommand not registered; stderr={result.stderr!r}"
    assert "locations" in result.stdout.lower() or "json" in result.stdout.lower()


def test_55_1_consumer_can_import_validate_locations_in_world() -> None:
    """AC-7 wiring (non-test caller via 55-1): Story 55-1's integration test
    at tests/integration/test_pf_validate_locations_on_materialized.py
    consumes ``validate_locations_in_world`` via importorskip. Once 54-3
    lands, the import must succeed — i.e. the same name + signature this
    test exercises is the EXACT one 55-1 will hit when its skip lifts.
    """
    # importing here mirrors the importorskip target in 55-1's test
    from sidequest.cli.validate import locations as mod

    assert hasattr(mod, "validate_locations_in_world"), (
        "55-1's post-materialize test imports this name — it must exist"
    )
    assert callable(mod.validate_locations_in_world)


# ---------------------------------------------------------------------------
# AC-6: Multi-pack discovery
# ---------------------------------------------------------------------------


def test_multi_pack_root_walks_every_pack() -> None:
    """AC-6 / AC-1: when given a directory containing multiple packs, the
    validator scans them all and aggregates issues per-pack."""
    res = validate_packs([FIXTURES])  # the fixtures root contains many packs
    # Aggregated errors from all fixture packs that contain hard errors.
    packs_with_errors = {i.pack for i in res.errors}
    assert "wf_duplicate_id" in packs_with_errors
    assert "wf_real_object_no_binding" in packs_with_errors
    assert "binding_bad_npc" in packs_with_errors
    # And the clean pack must not appear:
    assert "wf_ok" not in packs_with_errors
    assert "binding_location_feature_ok" not in packs_with_errors
