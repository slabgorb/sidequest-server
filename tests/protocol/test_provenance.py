"""Tests for Tier, Span, ContributionKind, MergeStep, Provenance.

These types have no dedicated test file in the Rust source (provenance.rs
has no inline unit_tests module). Tests here exercise construction,
serde round-trips, and wire-string values for each enum, providing
equivalent coverage to what would exist in idiomatic Rust.
"""

from __future__ import annotations

import json

from sidequest.protocol.provenance import (
    ContributionKind,
    MergeStep,
    Provenance,
    Span,
    Tier,
)

# ---------------------------------------------------------------------------
# Tier — enum wire strings (serde rename_all = "lowercase")
# ---------------------------------------------------------------------------


def test_tier_wire_strings() -> None:
    assert Tier.global_.value == "global"
    assert Tier.genre.value == "genre"
    assert Tier.world.value == "world"
    assert Tier.culture.value == "culture"


def test_tier_serializes_as_lowercase() -> None:

    from pydantic import RootModel

    class _Wrap(RootModel[Tier]):
        pass

    assert _Wrap(Tier.global_).model_dump_json() == '"global"'
    assert _Wrap(Tier.genre).model_dump_json() == '"genre"'
    assert _Wrap(Tier.world).model_dump_json() == '"world"'
    assert _Wrap(Tier.culture).model_dump_json() == '"culture"'


def test_tier_round_trip() -> None:
    for tier in Tier:
        assert Tier(tier.value) == tier


# ---------------------------------------------------------------------------
# ContributionKind — enum wire strings (serde rename_all = "snake_case")
# ---------------------------------------------------------------------------


def test_contribution_kind_wire_strings() -> None:
    assert ContributionKind.initial.value == "initial"
    assert ContributionKind.replaced.value == "replaced"
    assert ContributionKind.appended.value == "appended"
    assert ContributionKind.merged.value == "merged"


def test_contribution_kind_round_trip() -> None:
    for kind in ContributionKind:
        assert ContributionKind(kind.value) == kind


# ---------------------------------------------------------------------------
# Span — construction and round-trip
# ---------------------------------------------------------------------------


def test_span_construction() -> None:
    span = Span(start_line=1, start_col=0, end_line=3, end_col=12)
    assert span.start_line == 1
    assert span.start_col == 0
    assert span.end_line == 3
    assert span.end_col == 12


def test_span_round_trip_json() -> None:
    span = Span(start_line=5, start_col=2, end_line=7, end_col=20)
    json_str = span.model_dump_json()
    back = Span.model_validate_json(json_str)
    assert back == span


# ---------------------------------------------------------------------------
# MergeStep — construction and round-trip
# ---------------------------------------------------------------------------


def test_merge_step_with_span() -> None:
    step = MergeStep(
        tier=Tier.genre,
        file="genre_packs/caverns/rules.yaml",
        span=Span(start_line=10, start_col=0, end_line=12, end_col=5),
        contribution=ContributionKind.initial,
    )
    assert step.tier == Tier.genre
    assert step.file == "genre_packs/caverns/rules.yaml"
    assert step.span is not None
    assert step.contribution == ContributionKind.initial


def test_merge_step_without_span() -> None:
    step = MergeStep(
        tier=Tier.world,
        file="worlds/flickering_reach/world.yaml",
        contribution=ContributionKind.replaced,
    )
    assert step.span is None


def test_merge_step_round_trip_json() -> None:
    step = MergeStep(
        tier=Tier.culture,
        file="cultures/tribal.yaml",
        span=Span(start_line=1, start_col=0, end_line=1, end_col=10),
        contribution=ContributionKind.appended,
    )
    json_str = step.model_dump_json()
    back = MergeStep.model_validate_json(json_str)
    assert back == step


def test_merge_step_tier_serializes_as_lowercase() -> None:
    step = MergeStep(
        tier=Tier.global_,
        file="defaults.yaml",
        contribution=ContributionKind.initial,
    )
    data = json.loads(step.model_dump_json())
    assert data["tier"] == "global"


def test_merge_step_contribution_serializes_as_snake_case() -> None:
    step = MergeStep(
        tier=Tier.genre,
        file="genre.yaml",
        contribution=ContributionKind.merged,
    )
    data = json.loads(step.model_dump_json())
    assert data["contribution"] == "merged"


# ---------------------------------------------------------------------------
# Provenance — construction and round-trip
# ---------------------------------------------------------------------------


def test_provenance_construction() -> None:
    prov = Provenance(
        source_tier=Tier.world,
        source_file="worlds/flickering_reach/world.yaml",
        source_span=None,
        merge_trail=[
            MergeStep(
                tier=Tier.global_,
                file="defaults.yaml",
                contribution=ContributionKind.initial,
            ),
            MergeStep(
                tier=Tier.genre,
                file="genre_packs/mutant_wasteland/rules.yaml",
                contribution=ContributionKind.replaced,
            ),
            MergeStep(
                tier=Tier.world,
                file="worlds/flickering_reach/world.yaml",
                contribution=ContributionKind.appended,
            ),
        ],
    )
    assert prov.source_tier == Tier.world
    assert len(prov.merge_trail) == 3
    assert prov.merge_trail[0].tier == Tier.global_
    assert prov.merge_trail[2].contribution == ContributionKind.appended


def test_provenance_round_trip_json() -> None:
    prov = Provenance(
        source_tier=Tier.culture,
        source_file="cultures/nomadic.yaml",
        source_span=Span(start_line=42, start_col=0, end_line=44, end_col=8),
        merge_trail=[
            MergeStep(
                tier=Tier.global_,
                file="global/defaults.yaml",
                contribution=ContributionKind.initial,
            ),
            MergeStep(
                tier=Tier.culture,
                file="cultures/nomadic.yaml",
                span=Span(start_line=42, start_col=0, end_line=44, end_col=8),
                contribution=ContributionKind.replaced,
            ),
        ],
    )
    json_str = prov.model_dump_json()
    back = Provenance.model_validate_json(json_str)
    assert back == prov


def test_provenance_empty_merge_trail() -> None:
    prov = Provenance(
        source_tier=Tier.global_,
        source_file="defaults.yaml",
        source_span=None,
        merge_trail=[],
    )
    json_str = prov.model_dump_json()
    back = Provenance.model_validate_json(json_str)
    assert back.merge_trail == []


def test_provenance_source_tier_wire_format() -> None:
    prov = Provenance(
        source_tier=Tier.genre,
        source_file="genre.yaml",
        merge_trail=[],
    )
    data = json.loads(prov.model_dump_json())
    assert data["source_tier"] == "genre"
