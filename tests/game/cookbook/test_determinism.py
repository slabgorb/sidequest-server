"""Determinism: RNG derives ONLY from (campaign_seed, expansion_id)
(spec §4.3 / Sünden Deep §11)."""

from __future__ import annotations

from sidequest.game.cookbook.assemble import region_rng


def test_same_inputs_same_sequence() -> None:
    a = region_rng("camp-1", "exp-7")
    b = region_rng("camp-1", "exp-7")
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]


def test_different_expansion_diverges() -> None:
    a = region_rng("camp-1", "exp-7")
    b = region_rng("camp-1", "exp-8")
    assert [a.random() for _ in range(5)] != [b.random() for _ in range(5)]
