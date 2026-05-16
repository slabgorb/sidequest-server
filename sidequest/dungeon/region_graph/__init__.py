"""Stage-1 region-graph generator + Jaquays invariants (spec: Beneath Sünden §5.1).

One contiguous, edge-expanding map. Each expansion is generated from
(campaign_seed, expansion_id), enforced against the five Jaquays
post-conditions via a re-roll loop, and attached with an incremental
global connectivity + loopfulness check.

Pure, dependency-free, deterministic pre-curation. No persistence, no
themes loader, no depth_score, no OTEL here — those land in later plans
(3/4/5/7); this package's GenerationReport is the span-ready contract.
"""

from sidequest.dungeon.region_graph.config import JaquaysConfig
from sidequest.dungeon.region_graph.depth import (
    DepthConfig,
    DepthReport,
    assign_depth_scores,
    level_bucket,
    level_phrase,
)
from sidequest.dungeon.region_graph.errors import ExpansionGenerationError
from sidequest.dungeon.region_graph.generator import (
    attach_expansion,
    generate_expansion,
)
from sidequest.dungeon.region_graph.invariants import (
    GenerationReport,
    check_invariants,
)
from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionEdge,
    RegionGraph,
    RegionNode,
)

__all__ = [
    "JaquaysConfig",
    "DepthConfig",
    "DepthReport",
    "assign_depth_scores",
    "level_bucket",
    "level_phrase",
    "ExpansionGenerationError",
    "GenerationReport",
    "Expansion",
    "RegionEdge",
    "RegionGraph",
    "RegionNode",
    "attach_expansion",
    "check_invariants",
    "generate_expansion",
]
