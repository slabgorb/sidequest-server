"""Top-level magic working validator.

Composes framework-side checks (plugin ∈ active_plugins, source ∈
allowed_sources, hard_limits, cost_types) with plugin-side validation
(plugin.validate_working).
"""

from __future__ import annotations

from sidequest.magic.models import (
    Flag,
    FlagSeverity,
    MagicWorking,
    WorldMagicConfig,
)
from sidequest.magic.plugin import get_plugin

# Mapping plugin_id → source. The MagicPlugin Protocol exposes plugin_id
# but not source (source lives on the Plugin descriptor model loaded from
# each plugin's YAML, not on the runtime instance), so we mirror it here.
# Forward-looking entries: learned_v1, divine_v1, bargained_for_v1 are
# named in the spec but not yet implemented — they live here so a world
# config that references them is rejected with a clean DEEP_RED at check
# #2 rather than a KeyError at check #5. Check #5 also defends explicitly.
_PLUGIN_SOURCE = {
    "innate_v1": "innate",
    "item_legacy_v1": "item_based",
    "learned_v1": "learned",
    "divine_v1": "divine",
    "bargained_for_v1": "bargained_for",
}


def validate(working: MagicWorking, config: WorldMagicConfig) -> list[Flag]:
    """Validate a magic working against a world's magic config.

    Returns a list of flags; empty = clean. Severity: yellow / red / deep_red.
    """
    flags: list[Flag] = []

    # 1. Plugin must be in this world's active_plugins
    if working.plugin not in config.active_plugins:
        flags.append(
            Flag(
                severity=FlagSeverity.DEEP_RED,
                reason="plugin_not_in_active_plugins",
                detail=(
                    f"world {config.world_slug!r} active_plugins={config.active_plugins}; "
                    f"got {working.plugin!r}"
                ),
            )
        )
        # Don't try plugin-side validation if plugin isn't even active.
        return flags

    # 2. Plugin's source must be in allowed_sources
    source = _PLUGIN_SOURCE.get(working.plugin)
    if source is None:
        flags.append(
            Flag(
                severity=FlagSeverity.DEEP_RED,
                reason="unknown_plugin_id",
                detail=working.plugin,
            )
        )
        return flags
    if source not in config.allowed_sources:
        flags.append(
            Flag(
                severity=FlagSeverity.DEEP_RED,
                reason="source_not_in_allowed_sources",
                detail=f"source={source} not in {config.allowed_sources}",
            )
        )

    # 3. Cost types must be in world's cost_types
    for cost_type in working.costs:
        if cost_type not in config.cost_types:
            flags.append(
                Flag(
                    severity=FlagSeverity.YELLOW,
                    reason="unknown_cost_type",
                    detail=f"cost_type={cost_type!r} not in world cost_types {config.cost_types}",
                )
            )

    # 4. Hard limits — keyword match in narrator_basis (v1 simple detector).
    # Substring match is intentionally crude: false positives are possible
    # for short keywords ("war" would match "warning"). Coyote Star's
    # hard_limits are long phrases ("resurrection", "ftl telepathy") so the
    # risk is low. Smarter detection (entity recognition, semantic match)
    # is a future iteration per spec §5d.
    basis_lower = working.narrator_basis.lower()
    for limit in config.hard_limits:
        keyword = limit.id.replace("no_", "").replace("_", " ")
        if keyword and keyword in basis_lower:
            flags.append(
                Flag(
                    severity=FlagSeverity.DEEP_RED,
                    reason=f"hard_limit_violation:{limit.id}",
                    detail=limit.description,
                )
            )

    # 5. Plugin-side validation. Defend against `_PLUGIN_SOURCE` having
    # forward-looking entries that aren't actually registered in
    # MAGIC_PLUGINS — a misconfigured world could otherwise crash the
    # validator with an unhandled KeyError.
    try:
        plugin = get_plugin(working.plugin)
    except KeyError:
        flags.append(
            Flag(
                severity=FlagSeverity.DEEP_RED,
                reason="plugin_known_but_not_registered",
                detail=(
                    f"plugin {working.plugin!r} appears in _PLUGIN_SOURCE but "
                    "is not present in MAGIC_PLUGINS — it has not been "
                    "implemented yet"
                ),
            )
        )
        return flags
    flags.extend(plugin.validate_working(working, config))

    # v1: DEEP_RED flags surface in OTEL but DO NOT interrupt narration.
    # The Locked-Decision-#7 "DEEP_RED can interrupt narration" path is a
    # deliberate FUTURE extension. To wire it, route any FlagSeverity.DEEP_RED
    # entry through an `on_deep_red_violation` hook called by the caller
    # (narration_apply.py) before the narration is delivered. The hook is
    # absent in v1 — flag-only emission is the explicit policy per
    # spec §5d "What this design does NOT catch" and the architect addendum
    # 2026-04-29 §5.2. Do not wire interruption without a follow-up story.

    return flags
