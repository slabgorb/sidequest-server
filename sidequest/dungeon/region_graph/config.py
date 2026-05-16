"""Tunable Jaquays thresholds + burst knobs (spec §5.1, all tunable in world config).

The integer fields are the *floors* (hard minimums). connection_burst drives
the actual counts well above the floors so a new area "pops in" wired into
many existing regions at once (spec decision 11a, §5.1 "Burst, not minimum").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JaquaysConfig:
    min_stitch_edges: int = 2
    min_loops_into_explored: int = 1
    min_hidden_edges: int = 1
    min_shortcut_edges: int = 1
    min_shortcut_gain: int = 1
    connection_burst: int = 3
    new_regions_per_expansion: tuple[int, int] = (3, 6)
    max_reroll_attempts: int = 64
    edge_kinds: tuple[str, ...] = (
        "corridor",
        "stairs",
        "shaft",
        "chute",
        "secret",
    )

    def validate(self) -> None:
        if not self.edge_kinds:
            raise ValueError("edge_kinds must be non-empty")
        if "secret" not in self.edge_kinds:
            raise ValueError("edge_kinds must include 'secret' (needed for hidden edges)")
        for name in (
            "min_stitch_edges",
            "min_loops_into_explored",
            "min_hidden_edges",
            "min_shortcut_edges",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.min_shortcut_gain < 1:
            raise ValueError("min_shortcut_gain must be >= 1")
        if self.connection_burst < 0:
            raise ValueError("connection_burst must be >= 0")
        if self.max_reroll_attempts < 1:
            raise ValueError("max_reroll_attempts must be >= 1")
        lo, hi = self.new_regions_per_expansion
        if lo < 1 or hi < lo:
            raise ValueError(
                f"new_regions_per_expansion must be (lo>=1, hi>=lo); "
                f"got {self.new_regions_per_expansion}"
            )
        if lo < self.min_stitch_edges:
            raise ValueError(
                "new_regions_per_expansion lower bound must be "
                ">= min_stitch_edges (need that many distinct new regions "
                "to form independent entries)"
            )
        if lo < self.min_stitch_edges + self.min_shortcut_edges:
            raise ValueError(
                "new_regions_per_expansion lower bound must be "
                ">= min_stitch_edges + min_shortcut_edges (need distinct new "
                "regions for >=2 independent entries AND distinct shortcut "
                "targets)"
            )
