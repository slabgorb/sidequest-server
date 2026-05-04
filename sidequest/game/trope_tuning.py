"""Story 45-27 — trope progression tuning constants.

Single home for the four playtest-tunable knobs that govern trope
tempo. Per CLAUDE.md "no silent fallbacks" + ADR-068 magic-literal
extraction, these live in one module so a single PR can adjust them
without grepping the codebase.

Initial values land per playtest 3 (Felix session, Sebastien direct
report) where mid-session pile-up of trope progressions blurred
narrative threads. The cap (3), brake (0.5), and cooldown (2 turns)
are conservative starting points — playtest can pull them down if the
pile-up returns or push them up if the world feels too quiet.

Per-genre overrides are out of scope for 45-27. A future story may let
``genre_packs/<g>/pack.yaml`` override these; until then the values
here are global.
"""

from __future__ import annotations

MAX_SIMULTANEOUS_ACTIVE: int = 3
"""Hard ceiling on the number of tropes whose status is ``progressing``
at any one time. The (N+1)th candidate stays dormant and emits
``trope.cap_blocked`` so the GM panel can show "engine refused"
distinctly from "engine never engaged".
"""

FIRE_COOLDOWN_TURNS: int = 2
"""Turns of suppression after any beat fires (or trope resolves)
before a new ``dormant → progressing`` transition is allowed. Already-
progressing tropes continue to advance during cooldown — the cooldown
gates *new activations*, not progression.
"""

FOREGROUND_K: int = 2
"""Number of progressing tropes whose beat directives reach the
narrator prompt's Early zone (load-bearing). The remainder, up to
``MAX_SIMULTANEOUS_ACTIVE``, render as a Valley-zone summary so the
narrator still has background context without diluting attention
across every active thread.
"""

PROGRESSION_RATE_MULTIPLIER: float = 0.5
"""Global brake applied to every trope's YAML-declared
``rate_per_turn`` during the tick. Half the playtest-3 pile-up was
simply too-fast progression; this halves it before any per-genre
authoring change is needed.
"""
