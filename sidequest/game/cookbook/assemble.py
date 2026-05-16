"""assemble_region — the deterministic region-content contract.

Spec §4.3: a pure function oq-1's materializer invokes. All randomness
derives ONLY from (campaign_seed, expansion_id) per Sünden Deep §11.
NO CR→Edge translation here (oq-1 materializer seam, ADR-014/078).
"""

from __future__ import annotations

import hashlib
import random

from sidequest.game.cookbook.models import Affinities, CrBand, SizeBudget


def region_rng(campaign_seed: str, expansion_id: str) -> random.Random:
    """A Random seeded purely by (campaign_seed, expansion_id)."""
    digest = hashlib.sha256(f"{campaign_seed}\x1f{expansion_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def band_for_depth(aff: Affinities, depth_score: float) -> CrBand:
    """First band whose depth_lt strictly exceeds depth_score.

    Bands are listed in increasing depth (spec §4.2). depth_lt is an
    exclusive upper bound; the last band's depth_lt (1.01) is the cap.
    """
    for band in aff.cr_bands:
        if depth_score < band.depth_lt:
            return band
    return aff.cr_bands[-1]


def budget_for_burst(aff: Affinities, burst_magnitude: int) -> SizeBudget:
    """First size_by_burst row whose burst_lte ≥ burst; else the largest.

    size_by_burst is listed in increasing burst (spec §4.2).
    """
    for row in aff.size_by_burst:
        if burst_magnitude <= row.burst_lte:
            return row
    return aff.size_by_burst[-1]
