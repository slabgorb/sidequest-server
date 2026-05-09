"""Visual palette tokens for the orbital chart renderer.

Original spec: docs/superpowers/specs/2026-05-02-orbital-chart-visual-restoration-design.md §5.
Orrery v2 spec:  docs/superpowers/specs/2026-05-04-orrery-v2-visual-restoration.md §4.2-§4.6, §5.

Star Wars: A New Hope HUD register — black ground, brass-amber phosphor, red
accents, white reserved for the party marker only.

Tokens are deliberately tiny and free-function-accessible (no class, no
container) — the renderer reads them as constants. Per-world palette
overrides via chart.yaml are out of scope for this restoration.
"""

# ---- Colors -------------------------------------------------------------

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

# ---- Fonts --------------------------------------------------------------

# Web-served fonts loaded by the UI; emit family with fallback so the chart
# stays legible if the @font-face hasn't loaded yet.
FONT_DISPLAY = "Orbitron, monospace"
FONT_NUMERIC = "VT323, monospace"

# ---- Bearing rose (§4.2) -----------------------------------------------

# Inner and outer rings of the bearing-rose dial. Pixel-units in chart-space
# (i.e. relative to chart center, before viewport scaling).
BEARING_ROSE_INNER_PX = 78
BEARING_ROSE_OUTER_PX = 84

# Tick lengths at every 10° / 30° / 90°. The longer ticks step outward from
# the outer ring.
BEARING_ROSE_TICK_LEN_10 = 4
BEARING_ROSE_TICK_LEN_30 = 6  # 1.5x
BEARING_ROSE_TICK_LEN_90 = 10  # 2.5x

# Cardinal numerals (000/090/180/270) and intermediates (030..330).
BEARING_ROSE_CARDINAL_FONT_SIZE = 9
BEARING_ROSE_INTERMEDIATE_FONT_SIZE = 7

# ---- Reticle vocabulary (§4.3) -----------------------------------------
#
# Two reticle scales share the same dash + stroke vocabulary but differ in
# radii. Star reticle marks the centered star at system_root; course reticle
# (in `course_render.py`) marks the course-target body.
#
# Per AC #16: course_render.py imports these constants instead of carrying
# its own private literals.

# Star reticle — large, marks COYOTE-the-star
STAR_RETICLE_OUTER_R = 28.0  # outer dashed ring
STAR_RETICLE_INNER_R = 20.0  # inner solid ring
STAR_RETICLE_TICK_INNER = 14.0  # crosshair tick from r=14
STAR_RETICLE_TICK_OUTER = 22.0  # to r=22
STAR_RETICLE_CORE_R = 6.0  # brass core disk
STAR_RETICLE_OUTER_STROKE = 2.0
STAR_RETICLE_INNER_STROKE = 1.2

# Course reticle — small, marks course targets in `course_render.py`
COURSE_RETICLE_OUTER_R = 13.0
COURSE_RETICLE_INNER_R = 7.0
COURSE_RETICLE_TICK_INNER = 9.0
COURSE_RETICLE_TICK_OUTER = 15.0

# Shared reticle vocabulary — both scales use the same dash pattern + stroke
# style for visual consistency.
RETICLE_DASH_PATTERN = "3 2"

# ---- Register styling (§4.4) -------------------------------------------

# Orbit ellipse stroke patterns by register. `chalk` orbits are dashed.
ORBIT_DASH_CHALK = "6 5 2 5"
ORBIT_OPACITY_CHALK = 0.85
ORBIT_STROKE_ENGRAVED = 0.6
ORBIT_STROKE_CHALK = 0.7

# Label styling by register.
# Engraved (default): Orbitron, weight 700, letter-spacing 2.
LABEL_ENGRAVED_FONT = FONT_DISPLAY
LABEL_ENGRAVED_WEIGHT = 700
LABEL_ENGRAVED_LETTER_SPACING = 2

# Chalk: Orbitron, weight 600, letter-spacing 3.
LABEL_CHALK_FONT = FONT_DISPLAY
LABEL_CHALK_WEIGHT = 600
LABEL_CHALK_LETTER_SPACING = 3

# Prose: VT323 italic, opacity 0.85.
LABEL_PROSE_FONT = FONT_NUMERIC
LABEL_PROSE_FONT_SIZE = 10
LABEL_PROSE_OPACITY = 0.85

# ---- Moon-band rendering at system-scope (§4.6) ------------------------

# When a parent body has children visible at system-root scope, moons are
# placed in a band of fixed pixel radii around the parent (real moon orbits
# are sub-pixel at system scope; the design inflates them).
MOON_BAND_INNER_PX = 24  # closest moon
MOON_BAND_STEP_PX = 18  # step outward per moon
MOON_BAND_MAX = 8  # overflow threshold — beyond this, fall back to +N glyph
MOON_DOT_R = 2.0  # moon body glyph at system-scope (small dot)

# ---- Label de-collision (§5) -------------------------------------------

# Padding outward from a body's glyph (radial-out anchor) before the label.
LABEL_RADIAL_PADDING_PX = 14

# Bearing rose clearance — body labels at system_root must sit at ≥
# BEARING_ROSE_OUTER_PX + LABEL_BEARING_ROSE_CLEARANCE radial distance from
# chart center, so they don't crash into the rose's degree numerals.
LABEL_BEARING_ROSE_CLEARANCE = 14

# Peer-collision tier — bodies clustered within MIN_ANGULAR_SEPARATION_DEG
# get progressive radial offsets to spread their labels outward.
MIN_ANGULAR_SEPARATION_DEG = 25
LABEL_TIER_RADIAL_OFFSET_PX = 12
LABEL_TIER_MAX = 3  # cap; beyond this, accept collision and warn

# ---- Hazard non-color signal (AC #11) ----------------------------------

# Color-blind accessibility: hazard bodies carry a dashed-outline glyph
# stroke in addition to the existing red fill. This gives a non-color cue
# for players who can't distinguish red from yellow.
HAZARD_GLYPH_DASH = "3 2"
HAZARD_GLYPH_STROKE_WIDTH = 1.4

# ---- ADR-094 callout strategy (label_strategy.py) ----------------------

# Text-width estimator — calibrated upper-bound char widths per register.
# Bias toward overestimate: if text genuinely fits radial we still pick
# callout, which is the safe failure mode (visible-and-correct vs.
# overlapping). Calibrated against UI-rendered bbox at LABEL_*_FONT_SIZE.
LABEL_ENGRAVED_CHAR_WIDTH_PX: float = 8.5  # Orbitron 700 + letter-spacing 2
LABEL_CHALK_CHAR_WIDTH_PX: float = 9.0  # Orbitron 600 + letter-spacing 3
LABEL_PROSE_CHAR_WIDTH_PX: float = 6.5  # VT323 italic at LABEL_PROSE_FONT_SIZE

# Safety factors per ADR-094 §Decision rule 2 ("× 1.2") and the same-or-larger
# recommendation for arc-length fit.
TEXTPATH_FIT_SAFETY: float = 1.2
ARC_FIT_SAFETY: float = 1.2

# Callout block geometry.
CALLOUT_BLOCK_PADDING_PX: float = 4.0
CALLOUT_BLOCK_LINE_HEIGHT_PX: float = 12.0
CALLOUT_BLOCK_TAG_LINE_HEIGHT_PX: float = 10.0
CALLOUT_BLOCK_INTER_BLOCK_GAP_PX: float = 6.0
CALLOUT_GROUP_BORDER_PX: float = 0.6
CALLOUT_GROUP_TITLE_HEIGHT_PX: float = 14.0

# Leader-line geometry.
LEADER_STROKE_WIDTH_PX: float = 1.0
LEADER_TERMINATOR_SIZE_PX: float = 3.0

# Gutter zone — width and minimum-viability threshold.
GUTTER_WIDTH_PX: float = 120.0
GUTTER_MIN_VIABLE_WIDTH_PX: float = 60.0  # below this, gutter is unavailable
GUTTER_INNER_MARGIN_PX: float = 8.0  # space between chart bbox and gutter

# Tag-line max length per ADR §Label-block content rule.
CALLOUT_TAG_MAX_CHARS: int = 24

# Sibling-group threshold — N or more children in a moon band form a
# grouped <PARENT> SYSTEM block; below this they are singleton callouts.
CALLOUT_GROUP_MIN_MEMBERS: int = 3
