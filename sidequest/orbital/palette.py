"""Visual palette tokens for the orbital chart renderer.

Spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §5.
Star Wars: A New Hope HUD register — black ground, brass-amber phosphor, red
accents, white reserved for the party marker only.

Tokens are deliberately tiny and free-function-accessible (no class, no
container) — the renderer reads them as constants. Per-world palette
overrides via chart.yaml are out of scope for this restoration.
"""

# Background
BG = "#000000"

# Brass-amber phosphor — orbits, planet glyphs, infrastructure, labels
BRASS = "#f5d020"

# Red — star, hazard-flagged bodies, anomaly outlines, danger HUD
RED = "#e62a18"

# Reserved for party marker only — no body should ever use this color
PARTY = "#ffffff"

# Dimmed brass for inactive scope hints, derelict bodies, dimmed labels
DIM = "#7a6810"

# Web-served fonts loaded by the UI; emit family with fallback so the chart
# stays legible if the @font-face hasn't loaded yet.
FONT_DISPLAY = "Orbitron, monospace"
FONT_NUMERIC = "VT323, monospace"
