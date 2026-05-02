"""Pydantic model invariants for the magic module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.magic.models import (
    Flag,
    FlagSeverity,
    HardLimit,  # noqa: F401 — import-smoke: verifies HardLimit is exported
    LedgerBarSpec,
    MagicWorking,
    WorldKnowledge,
    WorldMagicConfig,  # noqa: F401 — import-smoke: verifies WorldMagicConfig is exported
)


class TestWorldKnowledge:
    def test_primary_only(self):
        wk = WorldKnowledge(primary="acknowledged")
        assert wk.primary == "acknowledged"
        assert wk.local_register is None

    def test_with_local_register(self):
        wk = WorldKnowledge(primary="classified", local_register="folkloric")
        assert wk.primary == "classified"
        assert wk.local_register == "folkloric"

    def test_local_register_must_be_le_primary_in_awareness(self):
        # Awareness order: denied < folkloric < mythic_lapsed < esoteric
        # < classified < acknowledged. local_register cannot exceed primary.
        with pytest.raises(ValidationError, match="local_register"):
            WorldKnowledge(primary="classified", local_register="acknowledged")

    def test_local_register_equal_to_primary_is_allowed(self):
        # `<=` boundary: same level on both axes is valid.
        wk = WorldKnowledge(primary="classified", local_register="classified")
        assert wk.local_register == "classified"


class TestMagicWorking:
    def test_minimum_required_fields(self):
        w = MagicWorking(
            plugin="innate_v1",
            mechanism="condition",
            actor="Sira Mendes",
            costs={"sanity": 0.12},
            domain="psychic",
            narrator_basis="alien-tech proximity",
        )
        assert w.plugin == "innate_v1"
        assert w.costs["sanity"] == 0.12

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            MagicWorking(
                plugin="innate_v1",
                mechanism="condition",
                actor="Sira Mendes",
                costs={"sanity": 0.12},
                domain="psychic",
                narrator_basis="x",
                bogus_field="should fail",
            )

    def test_negative_cost_forbidden(self):
        with pytest.raises(ValidationError):
            MagicWorking(
                plugin="innate_v1",
                mechanism="condition",
                actor="x",
                costs={"sanity": -0.1},
                domain="psychic",
                narrator_basis="x",
            )


class TestLedgerBarSpec:
    def test_monotonic_down_with_threshold_low(self):
        spec = LedgerBarSpec(
            id="sanity",
            scope="character",
            direction="down",
            range=(0.0, 1.0),
            threshold_low=0.40,
            consequence_on_low_cross="auto-fire The Bleeding-Through",
            starts_at_chargen=1.0,
        )
        assert spec.direction == "down"
        assert spec.threshold_low == 0.40

    def test_monotonic_down_requires_threshold_low(self):
        with pytest.raises(ValidationError, match="threshold_low"):
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                starts_at_chargen=1.0,
            )

    def test_threshold_outside_range_rejected(self):
        # threshold_low above range[1] would never trigger — fail loudly.
        with pytest.raises(ValidationError, match="must lie within range"):
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(0.0, 1.0),
                threshold_low=1.5,
                starts_at_chargen=1.0,
            )

    def test_inverted_range_rejected(self):
        with pytest.raises(ValidationError, match="must satisfy lo < hi"):
            LedgerBarSpec(
                id="sanity",
                scope="character",
                direction="down",
                range=(1.0, 0.0),
                threshold_low=0.4,
                starts_at_chargen=1.0,
            )

    def test_bidirectional_requires_both_thresholds(self):
        with pytest.raises(ValidationError, match="threshold"):
            LedgerBarSpec(
                id="bond",
                scope="item",
                direction="bidirectional",
                range=(-1.0, 1.0),
                threshold_high=0.7,
                # missing threshold_low
                starts_at_chargen=0.0,
            )


class TestFlag:
    def test_flag_construction(self):
        f = Flag(
            severity=FlagSeverity.RED,
            reason="plugin_not_in_allowed_sources",
            detail="bargained_for_v1",
        )
        assert f.severity == FlagSeverity.RED
