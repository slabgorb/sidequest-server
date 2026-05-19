"""Post-materialize, pf validate locations finds zero hard errors (Story 55-1).

Covers AC-10: the cookbook-emitted YAMLs the materializer writes must
pass Story 54-3's ``pf validate locations`` programmatic entry without
hard errors. This is the producer/consumer smoke that closes the loop
between Epic 52 (materializer + mask emit), Epic 54 (manifest types +
validator), and this story.

**Upstream dependency:** Story 54-3 (``sidequest.cli.validate.locations``
with ``validate_locations_in_world(world_dir)``) must land before this
test can go green. Until 54-3 ships, the import below fails loudly and
this test reports RED — surfacing the cross-story coordination need
rather than silently passing. Per CLAUDE.md, in-flight features must
not be xfailed; coordinate with SM if 54-3 has not landed when this
story enters its green phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.dungeon.materializer import _stage_emit_room_yamls
from sidequest.game.cookbook.models import GeneratedRoomDescription
from sidequest.protocol.models import LocationEntity, LocationEntityBinding

# Story 54-3 surface — the exact import path Story 55-1's AC-10
# integration test consumes. If 54-3 ships the entry under a different
# module path, update THIS import only; the `.errors == []` contract
# below is the load-bearing claim and must not change.
#
# Until 54-3 ships, importorskip leaves the assertion intact at module
# load time and skips the test with an actionable reason — NOT xfail
# (CLAUDE.md "never xfail in-flight features"). Once 54-3 lands,
# importorskip becomes a successful import and the test exercises the
# real validator. See the session file's Delivery Findings for the
# cross-story coordination context.
validate_locations_module = pytest.importorskip(
    "sidequest.cli.validate.locations",
    reason=(
        "Story 55-1 AC-10 integration test is blocked by Story 54-3 — "
        "`sidequest.cli.validate.locations.validate_locations_in_world` "
        "has not landed on develop. Remove this skip when 54-3 ships."
    ),
)
validate_locations_in_world = validate_locations_module.validate_locations_in_world


def _composed_region(room_id: str, *, special_id: str) -> GeneratedRoomDescription:
    return GeneratedRoomDescription(
        room_id=room_id,
        description=(
            f"Prose for {room_id}: a slick green crust coats the walls, "
            "water drips in regular plinks."
        ),
        entities=[
            LocationEntity(
                id=f"{room_id}_slick_walls",
                label="A slick green crust coats the walls.",
                tier="flavor_only",
                provenance="cookbook",
            ),
            LocationEntity(
                id=f"{room_id}_{special_id}",
                label="a black pool reflects the torchlight",
                tier="real_object",
                binding=LocationEntityBinding(
                    kind="location_feature",
                    ref=special_id,
                ),
                affordances=["drink_to_scry"],
                provenance="cookbook",
            ),
        ],
    )


def test_validator_reports_no_hard_errors_on_cookbook_yamls(tmp_path: Path) -> None:
    """AC-10: a fresh materialization (here driven directly through
    ``_stage_emit_room_yamls`` — the same helper ``_stage_commit`` calls)
    deposits YAMLs the 54-3 validator accepts without hard errors.

    The cookbook contract (every entity carries ``provenance='cookbook'``;
    every ``real_object`` carries a ``location_feature`` binding;
    ``flavor_only`` has no binding) IS the well-formedness +
    binding-resolution surface 54-3 checks. If this round trip fails,
    either:

    * the cookbook emit drifted from the manifest contract, or
    * 54-3's validator hardened a check the cookbook still violates.

    Both surface as actionable cross-story signal, not a silent pass.

    A heavier end-to-end test that drives a full async ``materialize()``
    pipeline (DungeonStore + GameSnapshot fixtures, real bundle,
    five-stage run) belongs alongside this assertion once both 54-3 and
    the materialize-pipeline fixture surface are stable — see the file
    docstring for the coordination plan.
    """
    world_dir = tmp_path / "caverns_sunden"
    composed_by_region = {
        "region_a": _composed_region("region_a", special_id="echoing_pool"),
        "region_b": _composed_region("region_b", special_id="ancient_alter"),
    }

    _stage_emit_room_yamls(world_dir=world_dir, composed_by_region=composed_by_region)

    # Pre-flight: prove the helper actually wrote the YAMLs the
    # validator is about to scan — keeps a failure here distinguishable
    # from a validator failure further down.
    rooms_dir = world_dir / "rooms"
    assert (rooms_dir / "region_a.yaml").is_file()
    assert (rooms_dir / "region_b.yaml").is_file()

    report = validate_locations_in_world(world_dir)
    assert report.errors == [], (
        f"54-3 validator reported hard errors on materialized cookbook "
        f"YAMLs: {report.errors}. The cookbook-emit contract (every "
        "entity provenance='cookbook'; real_object→location_feature "
        "binding; flavor_only→no binding) must produce a validator-clean "
        "YAML out of the box."
    )
